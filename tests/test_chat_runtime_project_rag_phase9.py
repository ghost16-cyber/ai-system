from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.app.chat_runtime.contracts import ChatResponseMode, ChatRuntimeFailureReason
from backend.app.chat_runtime.service import CanonicalChatRuntimeService
from backend.app.database.migrations import apply_schema_migrations
from backend.app.local_ai.config import LocalAIConfiguration
from backend.app.local_ai.contracts import (
    CapabilityStatus,
    MemoryCapability,
    OllamaCapability,
    VRAMCapability,
)
from backend.app.local_ai.generation import LocalGenerationGateway
from backend.app.local_ai.provider import ProviderGenerationResponse, ProviderInspection
from backend.app.local_ai.service import LocalAIService
from backend.app.project_artifacts import ProjectArtifactStore
from backend.app.project_control.project_service import CanonicalProjectService
from backend.app.project_control.service import ProjectControlPlane
from backend.app.project_retrieval.contracts import CorpusIngestionRequest
from backend.app.project_retrieval.bindings import canonical_retrieval_authority_id
from backend.app.project_retrieval.service import ProjectRetrievalService


GIB = 1024**3
MODEL_TAG = "qwen-test:1.5b"
MODEL_PROFILE_ID = "configured-local-model"
VALID_RESPONSE = (
    '{"schema_version":"astra.local-ai.advisory-response.v1",'
    '"response":"bounded answer"}'
)


class FakeProvider:
    def __init__(self) -> None:
        self.generate_calls = 0

    def inspect(self, *, timeout_seconds: int) -> ProviderInspection:
        del timeout_seconds
        return ProviderInspection("test", (MODEL_TAG,))

    def generate(self, request, *, cancelled=None) -> ProviderGenerationResponse:
        self.generate_calls += 1
        return ProviderGenerationResponse(
            model=MODEL_TAG, response=VALID_RESPONSE,
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


def _local_ai_service(database: Path) -> tuple[LocalAIService, FakeProvider]:
    configuration = LocalAIConfiguration(
        generation_enabled=True, provider_type="ollama", endpoint_identity="http://127.0.0.1:11434",
        synthesis_model=MODEL_TAG, coder_model=MODEL_TAG, planner_model=MODEL_TAG,
        reviewer_model=MODEL_TAG, chat_model=MODEL_TAG, connection_timeout_seconds=2,
        generation_timeout_seconds=10, maximum_context_tokens=4096, maximum_output_tokens=1024,
        allow_cpu_fallback=True, gpu_exclusive_concurrency=True,
    )
    fake = FakeProvider()
    gateway = LocalGenerationGateway(database, configuration=configuration, provider_client=fake)
    service = LocalAIService(
        database, configuration=configuration, probe=_capabilities, generation_gateway=gateway,
    )
    service.initialize()
    service.capability_report(refresh=True)
    version = service.configuration_state().configuration_version.model_profiles[MODEL_PROFILE_ID]
    service.set_model_enabled(
        MODEL_PROFILE_ID, enabled=True, actor_id="test-user",
        expected_version=version, idempotency_key="enable-chat-model",
    )
    return service, fake


def _project_fixture(tmp_path: Path):
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src" / "parser.py").write_text(
        "def parse(value: str) -> str:\n    \"\"\"Return the normalized parser value.\"\"\"\n    return value.strip()\n",
        encoding="utf-8",
    )
    database = tmp_path / "astra.db"
    apply_schema_migrations(database)
    artifacts = ProjectArtifactStore(database)
    control = ProjectControlPlane(database, artifact_store=artifacts)
    control.initialize()
    artifacts.initialize()
    project = CanonicalProjectService(control, artifacts).create_project(
        conversation_id="chat-conversation-1",
        workspace_id="chat-workspace",
        repository_root=root,
        repository_root_fingerprint="chat-root",
        actor_id="local-user",
        idempotency_key="create-chat-project",
        folder_authority={
            "status": "completed", "action_id": "chat-workspace",
            "conversation_id": "chat-conversation-1", "workspace_id": "chat-workspace",
            "repository_root_fingerprint": "chat-root",
        },
        specification={
            "specification_id": "chat-spec", "specification_hash": "1" * 64, "revision": 1,
            "included_paths": ["src"], "excluded_paths": [], "allowed_operations": ["read"],
        },
        manifest={
            "manifest_hash": "2" * 64, "complete": True, "revision": 1,
            "entries": [{"path": "src/parser.py", "sha256": "3" * 64}],
        },
        plan={"revision": 1, "acceptance_criteria": [], "work_units": []},
    )
    retrieval = ProjectRetrievalService(database, control, artifacts)
    retrieval.initialize()
    run = control.get_project(project.project_run_id)
    scope = control.get_scope_revision(run.current_scope_revision_id)
    plan = control.get_plan_revision(run.current_plan_revision_id)
    repository_state = retrieval.compute_repository_state(root, scope.included_paths, scope.excluded_paths)
    binding = {
        "project_id": run.project_run_id, "conversation_id": run.conversation_id,
        "actor_id": run.actor_id, "workspace_id": run.workspace_id,
        "repository_root": run.repository_root, "scope_revision_id": scope.scope_revision_id,
        "scope_hash": scope.content_hash, "plan_revision_id": plan.plan_revision_id,
        "plan_hash": plan.content_hash, "repository_manifest_hash": run.current_manifest_hash,
        "repository_state_hash": repository_state, "expected_project_state_version": run.state_version,
        "authority_id": canonical_retrieval_authority_id(run),
    }
    retrieval.ingest_project_corpus(CorpusIngestionRequest(**binding, idempotency_key="chat-ingest"))
    local_ai, provider = _local_ai_service(database)
    runtime = CanonicalChatRuntimeService(
        local_ai_service=local_ai, project_control=control, project_retrieval_service=retrieval,
    )
    return runtime, provider, project.project_run_id


def test_project_bound_retrieval_attaches_citations_to_the_answer(tmp_path: Path) -> None:
    runtime, provider, project_run_id = _project_fixture(tmp_path)

    result = runtime.answer(
        chat_request_id="chat-request-1",
        chat_run_id="run-chat-request-1",
        conversation_id="chat-conversation-1",
        message="normalized parser value",
        project_run_id=project_run_id,
        specialist="rag_specialist",
        intent="rag",
        confidence=0.8,
        safety_decision="allow",
        runtime_decision="fallback",
        memory_summary=None,
        use_rag=True,
        request_fingerprint="f" * 64,
        timeout_seconds=5,
    )

    assert result.response_mode == ChatResponseMode.LOCAL_AI
    assert result.lineage.retrieval is not None
    assert result.lineage.retrieval.evidence_count > 0
    assert result.lineage.retrieval.citations[0].relative_path == "src/parser.py"
    assert provider.generate_calls == 1


def test_retrieval_for_a_project_bound_to_a_different_conversation_is_a_typed_failure(
    tmp_path: Path,
) -> None:
    runtime, provider, project_run_id = _project_fixture(tmp_path)

    result = runtime.answer(
        chat_request_id="chat-request-2",
        chat_run_id="run-chat-request-2",
        conversation_id="a-different-conversation",
        message="normalized parser value",
        project_run_id=project_run_id,
        specialist="rag_specialist",
        intent="rag",
        confidence=0.8,
        safety_decision="allow",
        runtime_decision="fallback",
        memory_summary=None,
        use_rag=True,
        request_fingerprint="f" * 64,
        timeout_seconds=5,
    )

    assert result.lineage.retrieval is None
    # Generation still proceeds without project evidence -- retrieval failing
    # never blocks the chat turn.
    assert result.response_mode == ChatResponseMode.LOCAL_AI
    assert provider.generate_calls == 1


def test_no_generic_workspace_scan_occurs_when_no_canonical_project_is_selected(
    tmp_path: Path,
) -> None:
    runtime, provider, _ = _project_fixture(tmp_path)

    result = runtime.answer(
        chat_request_id="chat-request-3",
        chat_run_id="run-chat-request-3",
        conversation_id="chat-conversation-1",
        message="normalized parser value",
        project_run_id=None,
        specialist="rag_specialist",
        intent="rag",
        confidence=0.8,
        safety_decision="allow",
        runtime_decision="fallback",
        memory_summary=None,
        use_rag=True,
        request_fingerprint="f" * 64,
        timeout_seconds=5,
    )

    assert result.lineage.retrieval is None
