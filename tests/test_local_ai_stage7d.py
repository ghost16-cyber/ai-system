from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.database.migrations import apply_schema_migrations
from backend.app.local_ai.config import LocalAIConfiguration
from backend.app.local_ai.contracts import (
    CapabilityStatus,
    MemoryCapability,
    OllamaCapability,
    SchedulerStatus,
    VRAMCapability,
)
from backend.app.local_ai.generation import LocalGenerationGateway
from backend.app.local_ai.generation_contracts import (
    ChatGenerationTargetStatus,
    GenerationFailureReason,
    GenerationParameters,
    GenerationPurpose,
    LocalAIExecutionRequest,
    LocalAIExecutionState,
    LocalGenerationResult,
)
from backend.app.local_ai.provider import (
    ProviderClientError,
    ProviderErrorCode,
    ProviderGenerationResponse,
    ProviderInspection,
)
from backend.app.local_ai.routes import create_local_ai_router
from backend.app.local_ai.service import LocalAIService
from tests.test_project_synthesis_orchestrator import (
    _gateway as _synthesis_gateway,
    _runtime as _synthesis_runtime,
)


GIB = 1024**3
MODEL_TAG = "qwen-test:1.5b"
MODEL_PROFILE_ID = "configured-local-model"
VALID_RESPONSE = (
    '{"schema_version":"astra.local-ai.advisory-response.v1",'
    '"response":"bounded answer"}'
)


class FakeProvider:
    def __init__(
        self,
        *,
        response: str = VALID_RESPONSE,
        error: ProviderClientError | None = None,
        reported_model: str = MODEL_TAG,
    ) -> None:
        self.response = response
        self.error = error
        self.reported_model = reported_model
        self.inspect_calls = 0
        self.generate_calls = 0

    def inspect(self, *, timeout_seconds: int) -> ProviderInspection:
        del timeout_seconds
        self.inspect_calls += 1
        if self.error and self.error.code == ProviderErrorCode.UNREACHABLE:
            raise self.error
        return ProviderInspection("test", (MODEL_TAG,))

    def generate(self, request, *, cancelled=None) -> ProviderGenerationResponse:
        self.generate_calls += 1
        if self.error:
            raise self.error
        if cancelled is not None and cancelled():
            raise ProviderClientError(ProviderErrorCode.CANCELLED, "cancelled")
        return ProviderGenerationResponse(
            model=self.reported_model,
            response=self.response,
            metadata={"prompt_eval_count": 8, "eval_count": 3},
        )


def _configuration(*, chat_model: str | None = None) -> LocalAIConfiguration:
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


def _capabilities(*, available_vram: int = 8 * GIB):
    now = datetime.now(timezone.utc)
    return (
        MemoryCapability(
            capability_id="memory",
            status=CapabilityStatus.AVAILABLE,
            total_bytes=16 * GIB,
            available_bytes=12 * GIB,
            probed_at=now,
        ),
        VRAMCapability(
            capability_id="vram",
            status=CapabilityStatus.AVAILABLE,
            total_bytes=8 * GIB,
            free_bytes=available_vram,
            probed_at=now,
        ),
        OllamaCapability(
            capability_id="ollama",
            status=CapabilityStatus.AVAILABLE,
            endpoint="http://127.0.0.1:11434",
            configured_models=(MODEL_TAG,),
            installed_models=(MODEL_TAG,),
            provider_reachable=True,
            probed_at=now,
        ),
    )


def _service(
    tmp_path: Path,
    provider: FakeProvider | None = None,
    *,
    available_vram: int = 8 * GIB,
    database: Path | None = None,
) -> tuple[LocalAIService, FakeProvider, Path]:
    database = database or tmp_path / "stage7d.db"
    apply_schema_migrations(database)
    configuration = _configuration()
    fake = provider or FakeProvider()
    gateway = LocalGenerationGateway(
        database,
        configuration=configuration,
        provider_client=fake,
    )
    service = LocalAIService(
        database,
        configuration=configuration,
        probe=lambda: _capabilities(available_vram=available_vram),
        generation_gateway=gateway,
    )
    service.initialize()
    service.capability_report(refresh=True)
    version = service.configuration_state().configuration_version.model_profiles[
        MODEL_PROFILE_ID
    ]
    service.set_model_enabled(
        MODEL_PROFILE_ID,
        enabled=True,
        actor_id="test-user",
        expected_version=version,
        idempotency_key="enable-stage7d-model",
    )
    return service, fake, database


def _request(service: LocalAIService, **updates) -> LocalAIExecutionRequest:
    version = service.configuration_state().configuration_version.model_profiles[
        MODEL_PROFILE_ID
    ]
    values = {
        "request_id": "runtime-request-1",
        "idempotency_key": "runtime-execution-1",
        "actor_id": "test-user",
        "model_profile_id": MODEL_PROFILE_ID,
        "exact_model_tag": MODEL_TAG,
        "expected_configuration_version": version,
        "purpose": GenerationPurpose.CODING,
        "system_instruction": "Return one bounded advisory response object.",
        "user_content": "Explain this inert example.",
        "timeout_seconds": 5,
        "parameters": GenerationParameters(maximum_output_tokens=128),
    }
    values.update(updates)
    return LocalAIExecutionRequest(**values)


def test_public_execution_uses_scheduler_gateway_and_validation(tmp_path: Path) -> None:
    service, provider, _ = _service(tmp_path)
    app = FastAPI()
    app.include_router(create_local_ai_router(service))

    with TestClient(app) as client:
        response = client.post(
            "/runtime/local-ai/generations",
            json=_request(service).model_dump(mode="json"),
        )

    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "succeeded"
    assert body["generation_result"]["structured_output"] == {
        "schema_version": "astra.local-ai.advisory-response.v1",
        "response": "bounded answer",
    }
    assert body["authority_granted"] is False
    assert body["scheduler_job"]["status"] == "completed"
    assert provider.inspect_calls == provider.generate_calls == 1


def test_scheduler_submission_and_provenance_are_durable(tmp_path: Path) -> None:
    service, _, database = _service(tmp_path)

    result = service.execute_generation(_request(service))

    assert result.scheduler_job.status == SchedulerStatus.COMPLETED
    assert result.provenance is not None
    assert result.provenance.execution_id == (
        f"runtime-generation:{result.scheduler_job.job_id}"
    )
    assert result.provenance.reasoning_content_persisted is False
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM local_ai_scheduler_jobs"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM local_ai_execution_provenance"
        ).fetchone()[0] == 1


def test_execution_preserves_canonical_lineage_and_schema_identity(
    tmp_path: Path,
) -> None:
    _, _, _, intent, _ = _synthesis_runtime(tmp_path, _synthesis_gateway())
    service, _, _ = _service(tmp_path, database=tmp_path / "astra.db")
    request = _request(
        service,
        project_run_id=intent.project_run_id,
        coordinator_intent_id=intent.coordinator_intent_id,
    )

    result = service.execute_generation(request)

    assert result.scheduler_job.project_run_id == request.project_run_id
    assert (
        result.scheduler_job.coordinator_intent_id
        == request.coordinator_intent_id
    )
    assert result.provenance is not None
    assert result.provenance.project_run_id == request.project_run_id
    assert (
        result.provenance.coordinator_intent_id
        == request.coordinator_intent_id
    )
    assert (
        result.provenance.configuration["response_schema"]
        == request.expected_response_schema_identity
    )
    assert (
        result.provenance.validation_result[
            "expected_response_schema_identity"
        ]
        == request.expected_response_schema_identity
    )


@pytest.mark.parametrize(
    ("project_run_id", "coordinator_intent_id", "expected_error"),
    [
        ("missing-project", None, "project_run_not_found"),
        (None, "orphan-intent", "coordinator_intent_requires_project_run"),
    ],
)
def test_execution_rejects_orphan_lineage_before_provider_or_scheduler(
    tmp_path: Path,
    project_run_id: str | None,
    coordinator_intent_id: str | None,
    expected_error: str,
) -> None:
    service, provider, _ = _service(tmp_path)

    with pytest.raises(ValueError, match=expected_error):
        service.execute_generation(_request(
            service,
            project_run_id=project_run_id,
            coordinator_intent_id=coordinator_intent_id,
        ))

    assert provider.inspect_calls == provider.generate_calls == 0
    assert service.scheduler_jobs() == ()


def test_execution_replay_reuses_scheduler_and_generation_records(tmp_path: Path) -> None:
    service, provider, database = _service(tmp_path)
    request = _request(service)

    first = service.execute_generation(request)
    replay = service.execute_generation(request)

    assert first.state == replay.state == LocalAIExecutionState.SUCCEEDED
    assert replay.scheduler_job.job_id == first.scheduler_job.job_id
    assert replay.generation_result is not None
    assert replay.generation_result.replayed is True
    assert provider.generate_calls == 1
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM local_ai_scheduler_jobs"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM local_ai_generation_invocations"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM local_ai_execution_provenance"
        ).fetchone()[0] == 1


@pytest.mark.parametrize(
    ("code", "expected_reason", "expected_status"),
    [
        (
            ProviderErrorCode.TIMEOUT,
            GenerationFailureReason.GENERATION_TIMEOUT,
            SchedulerStatus.TIMED_OUT,
        ),
        (
            ProviderErrorCode.REJECTED,
            GenerationFailureReason.PROVIDER_REJECTED_REQUEST,
            SchedulerStatus.FAILED,
        ),
    ],
)
def test_provider_failures_finalize_existing_scheduler_job(
    tmp_path: Path,
    code: ProviderErrorCode,
    expected_reason: GenerationFailureReason,
    expected_status: SchedulerStatus,
) -> None:
    service, _, _ = _service(
        tmp_path,
        FakeProvider(error=ProviderClientError(code, "safe provider failure")),
    )

    result = service.execute_generation(_request(service))

    assert result.state == LocalAIExecutionState.FAILED
    assert result.generation_result is not None
    assert result.generation_result.failure_reason == expected_reason
    assert result.scheduler_job.status == expected_status
    assert result.provenance is not None
    assert result.provenance.error_category == expected_reason.value


def test_provider_rejection_replay_preserves_one_consistent_terminal_outcome(
    tmp_path: Path,
) -> None:
    message = "The local model provider rejected the request."
    service, provider, database = _service(
        tmp_path,
        FakeProvider(
            error=ProviderClientError(ProviderErrorCode.REJECTED, message)
        ),
    )
    request = _request(service)

    first = service.execute_generation(request)
    replay = service.execute_generation(request)

    assert first.state == replay.state == LocalAIExecutionState.FAILED
    assert first.scheduler_job.status == replay.scheduler_job.status == SchedulerStatus.FAILED
    assert first.generation_result is not None
    assert replay.generation_result is not None
    assert (
        first.generation_result.failure_reason
        == replay.generation_result.failure_reason
        == GenerationFailureReason.PROVIDER_REJECTED_REQUEST
    )
    assert first.generation_result.user_message == message
    assert replay.generation_result.user_message == message
    assert replay.generation_result.replayed is False
    assert replay.generation_result.generation_id == first.generation_result.generation_id
    assert replay.generation_result.started_at == first.generation_result.started_at
    assert replay.generation_result.completed_at == first.generation_result.completed_at
    assert first.scheduler_job.error is not None
    assert first.scheduler_job.error.message == message
    assert first.provenance is not None
    assert replay.provenance is not None
    assert (
        first.provenance.error_category
        == replay.provenance.error_category
        == GenerationFailureReason.PROVIDER_REJECTED_REQUEST.value
    )
    assert first.scheduler_job.started_at is not None
    assert first.scheduler_job.completed_at is not None
    assert (
        first.scheduler_job.started_at
        <= first.generation_result.started_at
        <= first.generation_result.completed_at
        <= first.scheduler_job.completed_at
    )
    assert first.provenance.started_at == first.generation_result.started_at
    assert first.provenance.completed_at == first.generation_result.completed_at
    assert provider.inspect_calls == provider.generate_calls == 1
    with sqlite3.connect(database) as connection:
        invocation = connection.execute(
            "SELECT status, failure_classification, result_json "
            "FROM local_ai_generation_invocations"
        ).fetchone()
        scheduler_count = connection.execute(
            "SELECT COUNT(*) FROM local_ai_scheduler_jobs"
        ).fetchone()[0]
        provenance_count = connection.execute(
            "SELECT COUNT(*) FROM local_ai_execution_provenance"
        ).fetchone()[0]
    assert invocation[0] == "failed"
    assert invocation[1] == GenerationFailureReason.PROVIDER_REJECTED_REQUEST.value
    stored = LocalGenerationResult.model_validate_json(invocation[2])
    assert stored.failure_reason == GenerationFailureReason.PROVIDER_REJECTED_REQUEST
    assert stored.user_message == message
    assert scheduler_count == provenance_count == 1


def test_malformed_provider_output_fails_strict_validation(tmp_path: Path) -> None:
    service, provider, _ = _service(
        tmp_path,
        FakeProvider(response='{"schema_version":"wrong","response":"unsafe"}'),
    )

    result = service.execute_generation(_request(service))

    assert provider.generate_calls == 1
    assert result.state == LocalAIExecutionState.FAILED
    assert result.generation_result is not None
    assert (
        result.generation_result.failure_reason
        == GenerationFailureReason.TARGET_SCHEMA_VALIDATION_FAILED
    )
    assert result.scheduler_job.status == SchedulerStatus.FAILED


def test_malformed_provider_envelope_fails_closed(tmp_path: Path) -> None:
    service, provider, _ = _service(
        tmp_path,
        FakeProvider(reported_model="substituted-model:latest"),
    )

    result = service.execute_generation(_request(service))

    assert provider.generate_calls == 1
    assert result.state == LocalAIExecutionState.FAILED
    assert result.generation_result is not None
    assert (
        result.generation_result.failure_reason
        == GenerationFailureReason.MALFORMED_PROVIDER_RESPONSE
    )
    assert result.scheduler_job.status == SchedulerStatus.FAILED


def test_admission_rejection_is_scheduled_but_never_invokes_provider(
    tmp_path: Path,
) -> None:
    service, provider, _ = _service(tmp_path, available_vram=1)

    result = service.execute_generation(
        _request(service, allow_cpu_fallback=False, prefer_gpu=True)
    )

    assert result.state == LocalAIExecutionState.BLOCKED
    assert result.scheduler_job.status == SchedulerStatus.BLOCKED
    assert result.generation_result is None
    assert result.provenance is None
    assert provider.inspect_calls == provider.generate_calls == 0


def _chat_service(tmp_path: Path, *, chat_model: str | None = None) -> LocalAIService:
    database = tmp_path / "chat-target.db"
    apply_schema_migrations(database)
    configuration = _configuration(chat_model=chat_model)
    service = LocalAIService(
        database,
        configuration=configuration,
        probe=lambda: _capabilities(),
        generation_gateway=LocalGenerationGateway(
            database, configuration=configuration, provider_client=FakeProvider()
        ),
    )
    service.initialize()
    service.capability_report(refresh=True)
    return service


def test_resolve_chat_generation_target_is_not_configured_by_default(tmp_path: Path) -> None:
    service = _chat_service(tmp_path)

    target = service.resolve_chat_generation_target()

    assert target.status == ChatGenerationTargetStatus.CHAT_ROLE_NOT_CONFIGURED
    assert target.model_profile_id is None
    assert target.exact_model_tag is None


def test_resolve_chat_generation_target_resolves_an_enabled_matching_profile(
    tmp_path: Path,
) -> None:
    service = _chat_service(tmp_path, chat_model=MODEL_TAG)
    version = service.configuration_state().configuration_version.model_profiles[
        MODEL_PROFILE_ID
    ]
    service.set_model_enabled(
        MODEL_PROFILE_ID,
        enabled=True,
        actor_id="test-user",
        expected_version=version,
        idempotency_key="enable-chat-model",
    )

    target = service.resolve_chat_generation_target()

    assert target.status == ChatGenerationTargetStatus.RESOLVED
    assert target.model_profile_id == MODEL_PROFILE_ID
    assert target.exact_model_tag == MODEL_TAG
    assert target.configuration_version is not None
    assert target.estimated_model_bytes is not None and target.estimated_model_bytes > 0


def test_resolve_chat_generation_target_reports_disabled_model(tmp_path: Path) -> None:
    service = _chat_service(tmp_path, chat_model=MODEL_TAG)

    target = service.resolve_chat_generation_target()

    assert target.status == ChatGenerationTargetStatus.MODEL_PROFILE_DISABLED


def test_resolve_chat_generation_target_reports_role_mapping_mismatch(
    tmp_path: Path,
) -> None:
    service = _chat_service(tmp_path, chat_model=MODEL_TAG)
    version = service.configuration_state().configuration_version.model_profiles[
        MODEL_PROFILE_ID
    ]
    service.set_model_enabled(
        MODEL_PROFILE_ID,
        enabled=True,
        actor_id="test-user",
        expected_version=version,
        idempotency_key="enable-chat-model-mismatch",
    )
    role_version = service.configuration_state().configuration_version.role_mappings.get(
        "chat", 0
    )
    service.set_role_mapping(
        "chat",
        "fake-deterministic",
        actor_id="test-user",
        expected_version=role_version,
        idempotency_key="map-chat-to-fake-deterministic",
    )

    target = service.resolve_chat_generation_target()

    assert target.status == ChatGenerationTargetStatus.ROLE_MAPPING_MISMATCH
