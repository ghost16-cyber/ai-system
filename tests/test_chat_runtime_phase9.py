from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.app.chat_runtime.contracts import ChatResponseMode, ChatRuntimeFailureReason
from backend.app.chat_runtime.service import CanonicalChatRuntimeService
from backend.app.database.migrations import apply_schema_migrations
from backend.app.database.repository import AnalysisRepository
from backend.app.local_ai.config import LocalAIConfiguration
from backend.app.local_ai.contracts import (
    CapabilityStatus,
    MemoryCapability,
    OllamaCapability,
    VRAMCapability,
)
from backend.app.local_ai.generation import LocalGenerationGateway
from backend.app.local_ai.provider import (
    ProviderClientError,
    ProviderErrorCode,
    ProviderGenerationResponse,
    ProviderInspection,
)
from backend.app.local_ai.service import LocalAIService


GIB = 1024**3
MODEL_TAG = "qwen-test:1.5b"
MODEL_PROFILE_ID = "configured-local-model"
VALID_RESPONSE = (
    '{"schema_version":"astra.local-ai.advisory-response.v1",'
    '"response":"bounded answer"}'
)


class FakeProvider:
    def __init__(self, *, response: str = VALID_RESPONSE, error: ProviderClientError | None = None) -> None:
        self.response = response
        self.error = error
        self.inspect_calls = 0
        self.generate_calls = 0
        self.last_request = None

    def inspect(self, *, timeout_seconds: int) -> ProviderInspection:
        del timeout_seconds
        self.inspect_calls += 1
        if self.error and self.error.code == ProviderErrorCode.UNREACHABLE:
            raise self.error
        return ProviderInspection("test", (MODEL_TAG,))

    def generate(self, request, *, cancelled=None) -> ProviderGenerationResponse:
        self.generate_calls += 1
        self.last_request = request
        if self.error:
            raise self.error
        return ProviderGenerationResponse(
            model=MODEL_TAG,
            response=self.response,
            metadata={"prompt_eval_count": 8, "eval_count": 3},
        )


def _capabilities():
    now = datetime.now(timezone.utc)
    return (
        MemoryCapability(
            capability_id="memory", status=CapabilityStatus.AVAILABLE,
            total_bytes=16 * GIB, available_bytes=12 * GIB, probed_at=now,
        ),
        VRAMCapability(
            capability_id="vram", status=CapabilityStatus.AVAILABLE,
            total_bytes=8 * GIB, free_bytes=8 * GIB, probed_at=now,
        ),
        OllamaCapability(
            capability_id="ollama", status=CapabilityStatus.AVAILABLE,
            endpoint="http://127.0.0.1:11434", configured_models=(MODEL_TAG,),
            installed_models=(MODEL_TAG,), provider_reachable=True, probed_at=now,
        ),
    )


def _configuration(*, chat_model: str | None) -> LocalAIConfiguration:
    return LocalAIConfiguration(
        generation_enabled=True,
        provider_type="ollama",
        endpoint_identity="http://127.0.0.1:11434",
        synthesis_model=MODEL_TAG,
        coder_model=MODEL_TAG,
        planner_model=MODEL_TAG,
        reviewer_model=MODEL_TAG,
        chat_model=chat_model,
        connection_timeout_seconds=2,
        generation_timeout_seconds=10,
        maximum_context_tokens=4096,
        maximum_output_tokens=1024,
        allow_cpu_fallback=True,
        gpu_exclusive_concurrency=True,
    )


def _local_ai_service(
    tmp_path: Path,
    *,
    chat_model: str | None = MODEL_TAG,
    enable_model: bool = True,
    provider: FakeProvider | None = None,
) -> tuple[LocalAIService, FakeProvider]:
    database = tmp_path / "chat-runtime.db"
    apply_schema_migrations(database)
    configuration = _configuration(chat_model=chat_model)
    fake = provider or FakeProvider()
    gateway = LocalGenerationGateway(database, configuration=configuration, provider_client=fake)
    service = LocalAIService(
        database, configuration=configuration, probe=_capabilities, generation_gateway=gateway,
    )
    service.initialize()
    service.capability_report(refresh=True)
    if enable_model:
        version = service.configuration_state().configuration_version.model_profiles[MODEL_PROFILE_ID]
        service.set_model_enabled(
            MODEL_PROFILE_ID, enabled=True, actor_id="test-user",
            expected_version=version, idempotency_key="enable-chat-model",
        )
    return service, fake


def _service(
    tmp_path: Path, *, chat_model: str | None = MODEL_TAG, enable_model: bool = True,
    provider: FakeProvider | None = None,
) -> tuple[CanonicalChatRuntimeService, FakeProvider]:
    local_ai, fake = _local_ai_service(
        tmp_path, chat_model=chat_model, enable_model=enable_model, provider=provider,
    )
    runtime = CanonicalChatRuntimeService(
        local_ai_service=local_ai,
        project_control=None,  # type: ignore[arg-type]
        project_retrieval_service=None,
    )
    return runtime, fake


def _answer(runtime: CanonicalChatRuntimeService, *, chat_request_id: str = "chat-request-1", fingerprint: str = "f" * 64):
    return runtime.answer(
        chat_request_id=chat_request_id,
        chat_run_id=f"run-{chat_request_id}",
        conversation_id="conversation-1",
        message="What can Astra do?",
        project_run_id=None,
        specialist="general_specialist",
        intent="general",
        confidence=0.5,
        safety_decision="allow",
        runtime_decision="fallback",
        memory_summary=None,
        use_rag=True,
        request_fingerprint=fingerprint,
        timeout_seconds=5,
    )


def test_answer_uses_local_ai_when_chat_role_is_configured_and_enabled(tmp_path: Path) -> None:
    runtime, provider = _service(tmp_path)

    result = _answer(runtime)

    assert result.response_mode == ChatResponseMode.LOCAL_AI
    assert result.assistant_response == "bounded answer"
    assert result.used_real_slm is True
    assert result.model == MODEL_TAG
    assert result.lineage.generation is not None
    assert result.lineage.failure is None
    assert result.lineage.retrieval is None
    assert provider.generate_calls == 1


def test_answer_falls_back_when_chat_role_is_not_configured(tmp_path: Path) -> None:
    runtime, provider = _service(tmp_path, chat_model=None, enable_model=False)

    result = _answer(runtime)

    assert result.response_mode == ChatResponseMode.DETERMINISTIC_FALLBACK
    assert result.assistant_response is None
    assert result.used_real_slm is False
    assert result.fallback_reason == ChatRuntimeFailureReason.CHAT_ROLE_NOT_CONFIGURED.value
    assert result.lineage.generation is None
    assert result.lineage.failure is not None
    assert result.lineage.failure.reason == ChatRuntimeFailureReason.CHAT_ROLE_NOT_CONFIGURED
    assert provider.generate_calls == 0


def test_answer_falls_back_when_model_is_configured_but_not_enabled(tmp_path: Path) -> None:
    runtime, provider = _service(tmp_path, enable_model=False)

    result = _answer(runtime)

    assert result.response_mode == ChatResponseMode.DETERMINISTIC_FALLBACK
    assert result.fallback_reason == ChatRuntimeFailureReason.MODEL_PROFILE_DISABLED.value
    assert provider.generate_calls == 0


def test_answer_maps_provider_failure_to_a_typed_fallback(tmp_path: Path) -> None:
    error = ProviderClientError(ProviderErrorCode.UNREACHABLE, "connection refused")
    runtime, provider = _service(tmp_path, provider=FakeProvider(error=error))

    result = _answer(runtime)

    assert result.response_mode == ChatResponseMode.DETERMINISTIC_FALLBACK
    assert result.fallback_reason == ChatRuntimeFailureReason.PROVIDER_UNREACHABLE.value
    assert result.lineage.failure is not None
    assert result.lineage.failure.reason == ChatRuntimeFailureReason.PROVIDER_UNREACHABLE


def test_exact_retry_replays_the_stored_generation_without_reinvoking_the_provider(
    tmp_path: Path,
) -> None:
    runtime, provider = _service(tmp_path)

    first = _answer(runtime)
    second = _answer(runtime)

    assert provider.generate_calls == 1
    assert first.assistant_response == second.assistant_response == "bounded answer"
    assert second.lineage.generation is not None
    assert second.lineage.generation.replayed is True


def test_changing_the_fingerprint_produces_an_independent_generation(tmp_path: Path) -> None:
    runtime, provider = _service(tmp_path)

    _answer(runtime, fingerprint="a" * 64)
    _answer(runtime, fingerprint="b" * 64)

    assert provider.generate_calls == 2


def test_no_project_selected_means_no_retrieval_is_attempted(tmp_path: Path) -> None:
    runtime, _ = _service(tmp_path)

    result = _answer(runtime)

    assert result.lineage.retrieval is None


def test_record_chat_runtime_link_persists_immutable_lineage(tmp_path: Path) -> None:
    local_ai, _ = _local_ai_service(tmp_path)
    runtime = CanonicalChatRuntimeService(
        local_ai_service=local_ai, project_control=None, project_retrieval_service=None,  # type: ignore[arg-type]
    )
    repository = AnalysisRepository(local_ai.database_path)
    repository.initialize()
    repository.create_chat_conversation(
        conversation_id="conversation-1", created_at=datetime.now(timezone.utc),
    )
    repository.create_chat_request(
        request_id="chat-request-1", conversation_id="conversation-1",
        user_message="What can Astra do?",
        request_payload={"message": "What can Astra do?", "use_rag": True},
        created_at=datetime.now(timezone.utc),
    )

    result = _answer(runtime)
    with sqlite3.connect(local_ai.database_path) as connection:
        connection.execute(
            "INSERT INTO chat_runs (run_id, conversation_id, user_message, assistant_response, "
            "selected_specialist, intent, confidence, rag_used, rag_context_count, "
            "runtime_decision, safety_decision, created_at, trace_summary_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                result.lineage.chat_run_id, "conversation-1", "What can Astra do?",
                result.assistant_response or "", "general_specialist", "general", 0.5, 0, 0,
                "fallback", "allow", datetime.now(timezone.utc).isoformat(), "[]",
            ),
        )
        connection.commit()
    repository.record_chat_runtime_link(result.lineage, project_run_id=None)

    with sqlite3.connect(local_ai.database_path) as connection:
        row = connection.execute(
            "SELECT response_mode, terminal_outcome, generation_id FROM chat_runtime_links "
            "WHERE chat_request_id = ?",
            ("chat-request-1",),
        ).fetchone()
        assert row is not None
        assert row[0] == "local_ai"
        assert row[1] == "succeeded"
        assert row[2] == result.lineage.generation.generation_id

        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "UPDATE chat_runtime_links SET terminal_outcome = 'tampered' WHERE chat_request_id = ?",
                ("chat-request-1",),
            )

    # Deleting the owning conversation cascades the link away with its
    # chat_run/chat_request -- immutability protects in-place tampering, not
    # a full, deliberate conversation purge.
    with sqlite3.connect(local_ai.database_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("DELETE FROM chat_requests WHERE request_id = ?", ("chat-request-1",))
        connection.execute("DELETE FROM chat_runs WHERE run_id = ?", (result.lineage.chat_run_id,))
        connection.commit()
        remaining = connection.execute(
            "SELECT COUNT(*) FROM chat_runtime_links WHERE chat_request_id = ?",
            ("chat-request-1",),
        ).fetchone()[0]
        assert remaining == 0


def test_corpus_context_reaches_the_provider_prompt(tmp_path: Path) -> None:
    """rag.corpus_retrieval is untouched by Phase 9 -- its results must still
    reach the model, via the corpus_context parameter on answer()."""

    runtime, provider = _service(tmp_path)

    result = runtime.answer(
        chat_request_id="chat-request-1",
        chat_run_id="run-chat-request-1",
        conversation_id="conversation-1",
        message="How is the assignment report generated?",
        project_run_id=None,
        specialist="general_specialist",
        intent="general",
        confidence=0.5,
        safety_decision="allow",
        runtime_decision="fallback",
        memory_summary=None,
        use_rag=True,
        request_fingerprint="f" * 64,
        timeout_seconds=5,
        corpus_context="The assignment report is generated from the extracted brief.",
    )

    assert result.response_mode == ChatResponseMode.LOCAL_AI
    assert provider.last_request is not None
    assert "The assignment report is generated from the extracted brief." in provider.last_request.prompt


_FORBIDDEN_LEGACY_PATTERN = re.compile(r"slm_gateway\.chat_with_slm|rag_context_service\.rag_search")


def test_canonical_chat_source_never_references_the_legacy_gateways() -> None:
    """Static reachability check: slm_gateway.chat_with_slm and
    rag_context_service.rag_search must be unreachable from the canonical
    chat path. Legacy endpoints may keep calling them directly, but
    chat_workflow.py and backend/app/chat_runtime/ never may."""

    repo_root = Path(__file__).resolve().parent.parent
    checked_files = [repo_root / "backend" / "app" / "chat_workflow.py"]
    checked_files.extend(
        sorted((repo_root / "backend" / "app" / "chat_runtime").glob("*.py"))
    )
    assert len(checked_files) > 1, "expected chat_runtime package files to exist"
    for path in checked_files:
        text = path.read_text(encoding="utf-8")
        assert not _FORBIDDEN_LEGACY_PATTERN.search(text), (
            f"{path} references a forbidden legacy gateway call"
        )
        assert "backend.app.slm" not in text, f"{path} imports the legacy slm package"
        assert "backend.app.rag.context_service" not in text, (
            f"{path} imports the legacy rag.context_service module"
        )
