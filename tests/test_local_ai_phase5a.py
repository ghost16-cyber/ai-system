from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
from pathlib import Path
from typing import Literal

import pytest
from pydantic import Field, ValidationError

from backend.app.database.migrations import (
    SCHEMA_MIGRATIONS,
    apply_schema_migrations,
    assert_schema_compatible,
)
from backend.app.local_ai.config import LocalAIConfiguration, load_local_ai_configuration
from backend.app.local_ai.generation import LocalGenerationGateway
from backend.app.local_ai.generation import MAX_RESPONSE_SCHEMA_BYTES
from backend.app.local_ai.generation_contracts import (
    GenerationContextItem,
    GenerationFailureReason,
    GenerationParameters,
    GenerationPurpose,
    GenerationState,
    LocalGenerationRequest,
    LocalGenerationResult,
    MAX_CONTEXT_ITEM_CHARS,
    MAX_CONTEXT_ITEMS,
    MAX_SYSTEM_INSTRUCTION_CHARS,
    MAX_USER_CONTENT_CHARS,
)
from backend.app.local_ai.provider import (
    MAX_RESPONSE_SCHEMA_BYTES as PROVIDER_MAX_RESPONSE_SCHEMA_BYTES,
    OllamaProviderClient,
    ProviderClientError,
    ProviderErrorCode,
    ProviderGenerationRequest,
    ProviderGenerationResponse,
    ProviderInspection,
    _strip_ungrammatable_bounds,
)
from backend.app.project_analysis.model_synthesis.contracts import SynthesisResponse
from backend.app.project_control.contracts import StrictModel, content_hash


class Proposal(StrictModel):
    schema_version: Literal["test.proposal.v1"] = "test.proposal.v1"
    summary: str = Field(min_length=1, max_length=200)
    command: str | None = Field(default=None, max_length=200)
    patch: str | None = Field(default=None, max_length=500)


class ExpandedProposal(StrictModel):
    schema_version: Literal["test.proposal.v1"] = "test.proposal.v1"
    summary: str = Field(min_length=1, max_length=200)
    required_detail: str


class FakeProvider:
    def __init__(
        self,
        *,
        installed: tuple[str, ...] = ("qwen-test:1.5b",),
        response: str = '{"schema_version":"test.proposal.v1","summary":"safe"}',
        error: ProviderClientError | None = None,
        reported_model: str = "qwen-test:1.5b",
    ) -> None:
        self.installed = installed
        self.response = response
        self.error = error
        self.reported_model = reported_model
        self.inspect_calls = 0
        self.generate_calls = 0
        self.last_request = None

    def inspect(self, *, timeout_seconds: int) -> ProviderInspection:
        self.inspect_calls += 1
        if self.error and self.error.code == ProviderErrorCode.UNREACHABLE:
            raise self.error
        return ProviderInspection("test", self.installed)

    def generate(self, request, *, cancelled=None) -> ProviderGenerationResponse:
        self.generate_calls += 1
        self.last_request = request
        if self.error:
            raise self.error
        if cancelled is not None and cancelled():
            raise ProviderClientError(
                ProviderErrorCode.CANCELLED, "cancelled"
            )
        return ProviderGenerationResponse(
            model=self.reported_model,
            response=self.response,
            metadata={"prompt_eval_count": 12, "eval_count": 4},
        )


def _configuration(**updates) -> LocalAIConfiguration:
    values = {
        "generation_enabled": True,
        "provider_type": "ollama",
        "endpoint_identity": "http://127.0.0.1:11434",
        "synthesis_model": "qwen-test:1.5b",
        "coder_model": "qwen-test:1.5b",
        "planner_model": "qwen-test:1.5b",
        "reviewer_model": "qwen-test:1.5b",
        "connection_timeout_seconds": 2,
        "generation_timeout_seconds": 10,
        "maximum_context_tokens": 4096,
        "maximum_output_tokens": 1024,
        "allow_cpu_fallback": False,
        "gpu_exclusive_concurrency": True,
    }
    values.update(updates)
    return LocalAIConfiguration(**values)


def _request(**updates) -> LocalGenerationRequest:
    values = {
        "request_id": "request-1",
        "idempotency_key": "generation-key-1",
        "purpose": GenerationPurpose.CODING,
        "exact_model_tag": "qwen-test:1.5b",
        "system_instruction": "Return only the requested JSON object.",
        "user_content": "Prepare inert proposal data.",
        "context": (),
        "expected_response_schema_identity": "test.proposal.v1",
        "timeout_seconds": 5,
        "parameters": GenerationParameters(maximum_output_tokens=128),
        "correlation": {"workspace_id": "workspace-1"},
    }
    values.update(updates)
    return LocalGenerationRequest(**values)


def _gateway(
    tmp_path: Path, provider: FakeProvider | None = None, *, configuration=None
) -> tuple[LocalGenerationGateway, Path, FakeProvider]:
    database = tmp_path / "phase5a.db"
    apply_schema_migrations(database)
    fake = provider or FakeProvider()
    gateway = LocalGenerationGateway(
        database,
        configuration=configuration or _configuration(),
        provider_client=fake,
    )
    gateway.initialize()
    return gateway, database, fake


def test_canonical_configuration_owns_generation_feature_flag() -> None:
    enabled = load_local_ai_configuration(
        {"ASTRA_LOCAL_AI_GENERATION_ENABLED": "true"}
    )
    disabled = load_local_ai_configuration({"ASTRA_SLM_ENABLED": "false"})
    assert enabled.generation_enabled is True
    assert disabled.generation_enabled is False
    assert enabled.schema_version == "astra.local-ai.configuration.v3"


@pytest.mark.parametrize(
    "configuration,reason",
    [
        (_configuration(generation_enabled=False), GenerationFailureReason.LOCAL_AI_DISABLED),
        (_configuration(provider_type="unavailable"), GenerationFailureReason.PROVIDER_UNSUPPORTED),
    ],
)
def test_disabled_and_unsupported_configuration_fail_before_provider(
    tmp_path: Path, configuration, reason
) -> None:
    gateway, _, provider = _gateway(tmp_path, configuration=configuration)
    result = gateway.generate(_request(), Proposal)
    assert result.failure_reason == reason
    assert provider.inspect_calls == provider.generate_calls == 0


def test_exact_model_readiness_rejects_missing_and_similarly_named_tags(
    tmp_path: Path,
) -> None:
    provider = FakeProvider(installed=("qwen-test:latest", "qwen-test:1.5b-extra"))
    gateway, _, _ = _gateway(tmp_path, provider)
    result = gateway.generate(_request(), Proposal)
    assert result.failure_reason == GenerationFailureReason.EXACT_MODEL_UNAVAILABLE
    assert provider.generate_calls == 0


def test_unreachable_provider_is_typed_and_persisted(tmp_path: Path) -> None:
    provider = FakeProvider(
        error=ProviderClientError(ProviderErrorCode.UNREACHABLE, "unreachable")
    )
    gateway, database, _ = _gateway(tmp_path, provider)
    result = gateway.generate(_request(), Proposal)
    assert result.failure_reason == GenerationFailureReason.PROVIDER_UNREACHABLE
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT status FROM local_ai_generation_invocations"
        ).fetchone()[0] == "failed"


def test_successful_strict_generation_persists_exact_hashes_and_usage(
    tmp_path: Path,
) -> None:
    request = _request(
        context=(GenerationContextItem(item_id="file-1", content="untrusted"),)
    )
    gateway, database, provider = _gateway(tmp_path)
    result = gateway.generate(request, Proposal)
    assert result.state == GenerationState.SUCCEEDED
    assert result.structured_output == {
        "schema_version": "test.proposal.v1",
        "summary": "safe",
        "command": None,
        "patch": None,
    }
    assert result.usage.prompt_eval_count == 12
    assert result.authority_granted is False and result.advisory_only is True
    assert provider.last_request.model == request.exact_model_tag
    assert provider.last_request.maximum_output_tokens == 128
    assert provider.last_request.temperature == 0.0
    assert provider.last_request.exact_response_schema == Proposal.model_json_schema()
    expected_schema_hash = content_hash(Proposal.model_json_schema())
    assert provider.last_request.exact_response_schema_hash == expected_schema_hash
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT request_fingerprint, input_hash, context_hash, response_hash, "
            "status, result_json, diagnostic_json, response_schema_hash "
            "FROM local_ai_generation_invocations"
        ).fetchone()
        assert row[0] == content_hash({
            "request": request.model_dump(mode="json"),
            "response_schema_hash": expected_schema_hash,
        })
        assert row[1] == content_hash(
            {
                "system_instruction": request.system_instruction,
                "user_content": request.user_content,
            }
        )
        assert row[2] == content_hash(
            [item.model_dump(mode="json") for item in request.context]
        )
        assert row[3] == hashlib.sha256(provider.response.encode()).hexdigest()
        assert row[4] == "completed"
        assert "Prepare inert" not in row[5]
        assert "Prepare inert" not in row[6]
        assert row[7] == expected_schema_hash


def test_successful_exact_replay_does_not_contact_provider(tmp_path: Path) -> None:
    gateway, _, provider = _gateway(tmp_path)
    first = gateway.generate(_request(), Proposal)
    replay = gateway.generate(_request(), Proposal)
    assert first.state == GenerationState.SUCCEEDED
    assert replay.state == GenerationState.SUCCEEDED
    assert replay.replayed is True
    assert replay.replay_source_generation_id == first.generation_id
    assert provider.inspect_calls == provider.generate_calls == 1


def test_changed_payload_with_same_key_fails_closed(tmp_path: Path) -> None:
    gateway, _, provider = _gateway(tmp_path)
    assert gateway.generate(_request(), Proposal).state == GenerationState.SUCCEEDED
    conflict = gateway.generate(_request(user_content="changed"), Proposal)
    assert conflict.failure_reason == GenerationFailureReason.IDEMPOTENCY_CONFLICT
    assert provider.generate_calls == 1


def test_changed_exact_schema_hash_invalidates_replay(tmp_path: Path) -> None:
    gateway, database, provider = _gateway(tmp_path)
    assert gateway.generate(_request(), Proposal).state == GenerationState.SUCCEEDED
    conflict = gateway.generate(_request(), ExpandedProposal)
    assert conflict.failure_reason == GenerationFailureReason.IDEMPOTENCY_CONFLICT
    assert provider.generate_calls == 1
    with sqlite3.connect(database) as connection:
        stored_hash = connection.execute(
            "SELECT response_schema_hash FROM local_ai_generation_invocations"
        ).fetchone()[0]
    assert stored_hash == content_hash(Proposal.model_json_schema())
    assert stored_hash != content_hash(ExpandedProposal.model_json_schema())


@pytest.mark.parametrize(
    "error_code,reason",
    [
        (ProviderErrorCode.TIMEOUT, GenerationFailureReason.GENERATION_TIMEOUT),
        (ProviderErrorCode.CANCELLED, GenerationFailureReason.GENERATION_CANCELLED),
        (ProviderErrorCode.REJECTED, GenerationFailureReason.PROVIDER_REJECTED_REQUEST),
        (ProviderErrorCode.MALFORMED_RESPONSE, GenerationFailureReason.MALFORMED_PROVIDER_RESPONSE),
    ],
)
def test_provider_failures_are_typed_and_never_successfully_replayed(
    tmp_path: Path, error_code, reason
) -> None:
    provider = FakeProvider(error=ProviderClientError(error_code, "safe failure"))
    gateway, database, _ = _gateway(tmp_path, provider)
    first = gateway.generate(_request(), Proposal)
    second = gateway.generate(_request(), Proposal)
    assert first.failure_reason == reason
    assert second.state == GenerationState.FAILED
    assert second.replayed is False
    assert second.failure_reason == first.failure_reason
    assert second.user_message == first.user_message == "safe failure"
    assert second.generation_id == first.generation_id
    assert second.started_at == first.started_at
    assert second.completed_at == first.completed_at
    assert provider.generate_calls == 1
    expected_status = {
        ProviderErrorCode.TIMEOUT: "timed_out",
        ProviderErrorCode.CANCELLED: "cancelled",
    }.get(error_code, "failed")
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT status, failure_classification, result_json "
            "FROM local_ai_generation_invocations"
        ).fetchone()
    assert row[0] == expected_status
    assert row[1] == reason.value
    assert LocalGenerationResult.model_validate_json(row[2]) == first


@pytest.mark.parametrize(
    "response,reason",
    [
        ("```json\n{}\n```", GenerationFailureReason.INVALID_STRUCTURED_OUTPUT),
        ('prose {"schema_version":"test.proposal.v1","summary":"x"}', GenerationFailureReason.INVALID_STRUCTURED_OUTPUT),
        ("{broken", GenerationFailureReason.INVALID_STRUCTURED_OUTPUT),
        ('{"schema_version":"test.proposal.v1"}', GenerationFailureReason.TARGET_SCHEMA_VALIDATION_FAILED),
        ('{"schema_version":"test.proposal.v1","summary":"x","extra":1}', GenerationFailureReason.TARGET_SCHEMA_VALIDATION_FAILED),
        ('{"schema_version":"wrong","summary":"x"}', GenerationFailureReason.TARGET_SCHEMA_VALIDATION_FAILED),
        ('{"schema_version":"test.proposal.v1","summary":"first","summary":"second"}', GenerationFailureReason.INVALID_STRUCTURED_OUTPUT),
    ],
)
def test_structured_output_requires_strict_json_and_target_schema(
    tmp_path: Path, response: str, reason
) -> None:
    gateway, _, _ = _gateway(tmp_path, FakeProvider(response=response))
    assert gateway.generate(_request(), Proposal).failure_reason == reason


def test_incorrect_provider_reported_model_is_rejected(tmp_path: Path) -> None:
    gateway, _, _ = _gateway(
        tmp_path, FakeProvider(reported_model="qwen-test:latest")
    )
    assert (
        gateway.generate(_request(), Proposal).failure_reason
        == GenerationFailureReason.MALFORMED_PROVIDER_RESPONSE
    )


def test_provider_client_rejects_missing_response_field(monkeypatch) -> None:
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _maximum):
            return b'{"model":"qwen-test:1.5b"}'

    monkeypatch.setattr(
        "backend.app.local_ai.provider.url_request.urlopen",
        lambda *_args, **_kwargs: Response(),
    )
    client = OllamaProviderClient("http://127.0.0.1:11434")
    with pytest.raises(ProviderClientError) as caught:
        client.generate(
            ProviderGenerationRequest(
                model="qwen-test:1.5b",
                system_instruction="system",
                prompt="prompt",
                timeout_seconds=2,
                maximum_output_tokens=10,
            )
        )
    assert caught.value.code == ProviderErrorCode.MALFORMED_RESPONSE


def test_ollama_provider_submits_exact_json_schema_and_generic_json(monkeypatch) -> None:
    payloads: list[dict] = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _maximum):
            return b'{"model":"qwen-test:1.5b","response":"{}"}'

    def open_request(request, **_kwargs):
        payloads.append(json.loads(request.data.decode("utf-8")))
        return Response()

    monkeypatch.setattr(
        "backend.app.local_ai.provider.url_request.urlopen", open_request
    )
    client = OllamaProviderClient("http://127.0.0.1:11434")
    schema = Proposal.model_json_schema()
    schema_hash = content_hash(schema)
    client.generate(ProviderGenerationRequest(
        model="qwen-test:1.5b", system_instruction="system", prompt="prompt",
        timeout_seconds=2, maximum_output_tokens=10, temperature=1.5,
        exact_response_schema=schema, exact_response_schema_hash=schema_hash,
    ))
    client.generate(ProviderGenerationRequest(
        model="qwen-test:1.5b", system_instruction="system", prompt="prompt",
        timeout_seconds=2, maximum_output_tokens=10,
    ))
    # The transmitted format is the schema with minLength/maxLength stripped
    # (a large maxLength reproducibly crashes Ollama's grammar compiler --
    # see test_structured_schema_strips_bounds_ollama_cannot_grammar_compile)
    # but the hash-integrity check above still bound to the full, unmodified
    # schema: content_hash(schema) succeeded, proving the check validates
    # against Astra's exact contract, not the transmitted subset.
    assert payloads[0]["format"] == _strip_ungrammatable_bounds(schema)
    assert payloads[0]["format"] != schema
    assert payloads[0]["format"] != "json"
    assert payloads[0]["options"]["temperature"] == 0.0
    assert payloads[0]["stream"] is False
    assert payloads[1]["format"] == "json"


def _all_schema_keys(node) -> set[str]:
    keys: set[str] = set()
    if isinstance(node, dict):
        keys.update(node.keys())
        for value in node.values():
            keys |= _all_schema_keys(value)
    elif isinstance(node, list):
        for item in node:
            keys |= _all_schema_keys(item)
    return keys


def test_structured_schema_strips_bounds_ollama_cannot_grammar_compile(
    monkeypatch,
) -> None:
    """Regression: a JSON schema with maxLength: 65536 (Proposal/Advisory
    response bounds) reproducibly crashes the installed Ollama's structured-
    output model runner ('model runner has unexpectedly stopped'). The
    provider must strip minLength/maxLength from what it actually transmits,
    while still hash-validating the caller's full, unmodified schema."""
    payloads: list[dict] = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _maximum):
            return b'{"model":"qwen-test:1.5b","response":"{\\"summary\\":\\"ok\\"}"}'

    def open_request(request, **_kwargs):
        payloads.append(json.loads(request.data.decode("utf-8")))
        return Response()

    monkeypatch.setattr(
        "backend.app.local_ai.provider.url_request.urlopen", open_request
    )
    client = OllamaProviderClient("http://127.0.0.1:11434")
    schema = Proposal.model_json_schema()
    assert "maxLength" in _all_schema_keys(schema)
    schema_hash = content_hash(schema)

    client.generate(ProviderGenerationRequest(
        model="qwen-test:1.5b", system_instruction="system", prompt="prompt",
        timeout_seconds=2, maximum_output_tokens=10,
        exact_response_schema=schema, exact_response_schema_hash=schema_hash,
    ))

    transmitted_keys = _all_schema_keys(payloads[0]["format"])
    assert "minLength" not in transmitted_keys
    assert "maxLength" not in transmitted_keys
    # The rest of the schema (types, required, additionalProperties, const)
    # is preserved -- only the two ungrammatable bound keywords are removed.
    assert payloads[0]["format"]["type"] == "object"
    assert payloads[0]["format"]["required"] == ["summary"]


def test_provider_rejects_stale_schema_hash_before_http(monkeypatch) -> None:
    monkeypatch.setattr(
        "backend.app.local_ai.provider.url_request.urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("no HTTP")),
    )
    client = OllamaProviderClient("http://127.0.0.1:11434")
    with pytest.raises(ProviderClientError) as caught:
        client.generate(ProviderGenerationRequest(
            model="qwen-test:1.5b", system_instruction="system", prompt="prompt",
            timeout_seconds=2, maximum_output_tokens=10,
            exact_response_schema=Proposal.model_json_schema(),
            exact_response_schema_hash="0" * 64,
        ))
    assert caught.value.code == ProviderErrorCode.INVALID_REQUEST


@pytest.mark.parametrize(
    "schema,reason",
    [
        (["not", "an", "object"], GenerationFailureReason.INVALID_REQUEST),
        ({"type": "object", "unsupported": {1, 2}}, GenerationFailureReason.INVALID_REQUEST),
        ({"type": "object", "description": "x" * (MAX_RESPONSE_SCHEMA_BYTES + 1)}, GenerationFailureReason.REQUEST_TOO_LARGE),
    ],
)
def test_malformed_or_oversized_schema_fails_before_provider(
    tmp_path: Path, schema, reason
) -> None:
    class InvalidSchema:
        @classmethod
        def model_json_schema(cls):
            return schema

    gateway, database, provider = _gateway(tmp_path)
    result = gateway.generate(_request(), InvalidSchema)  # type: ignore[arg-type]
    assert result.failure_reason == reason
    assert provider.inspect_calls == provider.generate_calls == 0
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM local_ai_generation_invocations"
        ).fetchone()[0] == 0


def test_wrong_discriminated_operation_type_fails_strict_validation(tmp_path: Path) -> None:
    response = json.dumps({
        "contract_version": "astra.project-synthesis.response.v1",
        "request_id": "request-1",
        "summary": "invalid operation",
        "operations": [{"operation": "rename", "path": "app.py"}],
        "assumptions": [], "uncertainties": [], "model_confidence": "low",
        "requires_clarification": False, "clarification_question": None,
        "recommended_validation": [],
    }, separators=(",", ":"))
    gateway, _, provider = _gateway(tmp_path, FakeProvider(response=response))
    result = gateway.generate(
        _request(expected_response_schema_identity="astra.project-synthesis.response.v1"),
        SynthesisResponse,
    )
    assert result.failure_reason == GenerationFailureReason.TARGET_SCHEMA_VALIDATION_FAILED
    assert provider.generate_calls == 1


@pytest.mark.parametrize(
    "updates",
    [
        {"system_instruction": "x" * (MAX_SYSTEM_INSTRUCTION_CHARS + 1)},
        {"user_content": "x" * (MAX_USER_CONTENT_CHARS + 1)},
        {"context": tuple(GenerationContextItem(item_id=str(i), content="x") for i in range(MAX_CONTEXT_ITEMS + 1))},
        {"context": ({"item_id": "x", "content": "x" * (MAX_CONTEXT_ITEM_CHARS + 1)},)},
        {"context": tuple({"item_id": str(i), "content": "x" * 15_000} for i in range(5))},
        {"timeout_seconds": 0},
        {"parameters": {"temperature": 3.0}},
    ],
)
def test_invalid_or_oversized_request_contracts_fail_before_gateway(updates) -> None:
    with pytest.raises(ValidationError):
        _request(**updates)


def test_configured_aggregate_context_and_output_limits_fail_before_provider(
    tmp_path: Path,
) -> None:
    configuration = _configuration(maximum_context_tokens=256, maximum_output_tokens=64)
    gateway, _, provider = _gateway(tmp_path, configuration=configuration)
    too_large = gateway.generate(
        _request(
            user_content="x" * 2000,
            parameters=GenerationParameters(maximum_output_tokens=64),
        ),
        Proposal,
    )
    assert too_large.failure_reason == GenerationFailureReason.REQUEST_TOO_LARGE
    different = _request(
        idempotency_key="output-key",
        parameters=GenerationParameters(maximum_output_tokens=65),
    )
    invalid_output = gateway.generate(different, Proposal)
    assert invalid_output.failure_reason == GenerationFailureReason.INVALID_REQUEST
    assert provider.inspect_calls == 0


def test_completed_invocation_is_immutable_and_generated_authority_text_is_inert(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("generation must not execute subprocesses")
        ),
    )
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("generation must not execute subprocesses")
        ),
    )
    provider = FakeProvider(
        response=(
            '{"schema_version":"test.proposal.v1","summary":"proposal",'
            '"command":"rm -rf project","patch":"apply this patch"}'
        )
    )
    gateway, database, _ = _gateway(tmp_path, provider)
    before = database.stat().st_size
    result = gateway.generate(_request(), Proposal)
    assert result.state == GenerationState.SUCCEEDED
    assert result.structured_output["command"] == "rm -rf project"
    assert database.stat().st_size >= before
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM project_approval_grants").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM project_worker_requests").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM project_execution_dispatches").fetchone()[0] == 0
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "UPDATE local_ai_generation_invocations SET result_json = '{}'"
            )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute("DELETE FROM local_ai_generation_invocations")


def test_migration_12_owns_gateway_schema_and_shape_validation(tmp_path: Path) -> None:
    database = tmp_path / "migration-12.db"
    apply_schema_migrations(database, migrations=SCHEMA_MIGRATIONS[:12])
    assert assert_schema_compatible(database, migrations=SCHEMA_MIGRATIONS[:12]) == 12
    with sqlite3.connect(database) as connection:
        trigger = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' "
            "AND name LIKE 'local_ai_generation_%' ORDER BY name"
        ).fetchall()
    # Migration 12, as originally applied, owns the table, its indexes, and the
    # terminal-state immutability trigger only. The delete-protection trigger
    # was mistakenly folded into migration 12's definition after it had
    # already been applied to a live database; it now lives in migration 15
    # (see test_migration_15_adds_delete_protection_without_disturbing_migration_12).
    assert [row[0] for row in trigger] == ["local_ai_generation_terminal_immutable"]


def test_migration_14_adds_immutable_exact_response_schema_hash(tmp_path: Path) -> None:
    database = tmp_path / "migration-14.db"
    apply_schema_migrations(database, migrations=SCHEMA_MIGRATIONS[:13])
    result = apply_schema_migrations(database, migrations=SCHEMA_MIGRATIONS[:14])
    assert result.applied_versions == (14,)
    assert assert_schema_compatible(database, migrations=SCHEMA_MIGRATIONS[:14]) == 14
    with sqlite3.connect(database) as connection:
        columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(local_ai_generation_invocations)"
            )
        }
    assert "response_schema_hash" in columns
    assert MAX_RESPONSE_SCHEMA_BYTES == PROVIDER_MAX_RESPONSE_SCHEMA_BYTES


def test_migration_15_adds_delete_protection_without_disturbing_migration_12(tmp_path: Path) -> None:
    """Regression for a migration-checksum-integrity incident: migration 12's
    checksum must never again change after it has been applied. The
    delete-protection trigger that was previously (incorrectly) part of
    migration 12 is its own additive migration 15."""
    database = tmp_path / "migration-15.db"
    apply_schema_migrations(database, migrations=SCHEMA_MIGRATIONS[:12])
    migration_12_checksum = SCHEMA_MIGRATIONS[11].checksum
    with sqlite3.connect(database) as connection:
        recorded = connection.execute(
            "SELECT checksum FROM schema_migrations WHERE version = 12"
        ).fetchone()[0]
    assert recorded == migration_12_checksum

    result = apply_schema_migrations(database, migrations=SCHEMA_MIGRATIONS[:15])
    assert result.applied_versions == (13, 14, 15)
    assert assert_schema_compatible(database, migrations=SCHEMA_MIGRATIONS[:15]) == 15

    with sqlite3.connect(database) as connection:
        recorded_after = connection.execute(
            "SELECT checksum FROM schema_migrations WHERE version = 12"
        ).fetchone()[0]
        triggers = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger' "
                "AND name LIKE 'local_ai_generation_%'"
            )
        }
    assert recorded_after == migration_12_checksum
    assert triggers == {
        "local_ai_generation_terminal_immutable",
        "local_ai_generation_records_no_delete",
    }


def test_persistence_failure_prevents_provider_contact(tmp_path: Path) -> None:
    gateway, database, provider = _gateway(tmp_path)
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TABLE local_ai_generation_invocations")
    result = gateway.generate(_request(), Proposal)
    assert result.failure_reason == GenerationFailureReason.PERSISTENCE_FAILURE
    assert provider.inspect_calls == provider.generate_calls == 0
