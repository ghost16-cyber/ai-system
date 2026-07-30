from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from backend.app.database.migrations import apply_schema_migrations
from backend.app.local_ai.config import load_local_ai_configuration
from backend.app.local_ai.contracts import (
    CapabilityStatus,
    MemoryCapability,
    OllamaCapability,
    VRAMCapability,
)
from backend.app.local_ai.generation_contracts import GenerationPurpose, LocalAIExecutionRequest
from backend.app.local_ai.provider import ProviderGenerationResponse, ProviderInspection
from backend.app.local_ai.providers.llama_cpp import LlamaCppProviderAdapter
from backend.app.local_ai.providers.ollama import OllamaProviderAdapter
from backend.app.local_ai.providers.registry import ProviderNotRegisteredError, ProviderRegistry
from backend.app.local_ai.service import LocalAIService, default_provider_registry


GIB = 1024**3
MODEL = "qwen2.5-coder:1.5b"
LLAMA_CPP_MODEL = "qwen2.5-coder-7b-q4_k_m.gguf"


class _FakeClient:
    """Satisfies `LocalModelProviderClient` -- the same minimal shape every
    existing local_ai test fake already uses."""

    def __init__(self, *, model: str, response: str = '{"response": "ok"}') -> None:
        self.model = model
        self.response = response
        self.inspect_calls = 0
        self.generate_calls = 0

    def inspect(self, *, timeout_seconds: int) -> ProviderInspection:
        del timeout_seconds
        self.inspect_calls += 1
        return ProviderInspection(provider_version="test", installed_models=(self.model,), loaded_models=(self.model,))

    def generate(self, request, *, cancelled=None) -> ProviderGenerationResponse:
        del cancelled
        self.generate_calls += 1
        return ProviderGenerationResponse(
            model=self.model, response=self.response,
            metadata={"prompt_eval_count": 2, "eval_count": 1},
        )


def _capabilities(*, available_vram: int = 8 * GIB) -> tuple:
    now = datetime.now(timezone.utc)
    return (
        MemoryCapability(capability_id="memory", status=CapabilityStatus.AVAILABLE,
                         total_bytes=32 * GIB, available_bytes=16 * GIB, probed_at=now),
        VRAMCapability(capability_id="vram", status=CapabilityStatus.AVAILABLE,
                       total_bytes=8 * GIB, free_bytes=available_vram, probed_at=now),
        OllamaCapability(capability_id="ollama", status=CapabilityStatus.AVAILABLE,
                         endpoint="http://127.0.0.1:11434", configured_models=(MODEL,),
                         installed_models=(MODEL,), loaded_models=(MODEL,),
                         provider_reachable=True, configured_model_missing=False, probed_at=now),
    )


def _service(tmp_path: Path, *, configuration=None, provider_registry=None) -> LocalAIService:
    database = tmp_path / "phase8c.db"
    apply_schema_migrations(database)
    service = LocalAIService(
        database,
        configuration=configuration or load_local_ai_configuration({
            "ASTRA_LOCAL_AI_GENERATION_ENABLED": "true",
            "ASTRA_LOCAL_AI_MODEL": MODEL,
        }),
        probe=lambda: _capabilities(),
        provider_registry=provider_registry,
    )
    service.initialize()
    return service


def _enable_configured_model(service: LocalAIService) -> None:
    service.capability_report(refresh=True)
    version = service.configuration_state().configuration_version.model_profiles["configured-local-model"]
    service.set_model_enabled(
        "configured-local-model", enabled=True, actor_id="test-setup",
        expected_version=version, idempotency_key="enable-configured",
    )


def test_model_profile_resolves_through_the_registry(tmp_path: Path) -> None:
    service = _service(tmp_path)
    assert service.provider_registry.list_provider_ids() == (
        "ollama-local", "llama-cpp-local", "fake-deterministic",
    )
    resolved = service.provider_registry.get("ollama-local")
    assert isinstance(resolved, OllamaProviderAdapter)


def test_generation_works_with_ollama_through_the_registry(tmp_path: Path) -> None:
    fake_ollama = _FakeClient(model=MODEL)
    registry = ProviderRegistry()
    registry.register(OllamaProviderAdapter(endpoint_identity="http://127.0.0.1:11434", client=fake_ollama))
    service = _service(tmp_path, provider_registry=registry)
    _enable_configured_model(service)

    version = service.configuration_state().configuration_version.model_profiles["configured-local-model"]
    result = service.execute_structured_generation(
        LocalAIExecutionRequest(
            request_id="r1", idempotency_key="idem-ollama", actor_id="tester",
            model_profile_id="configured-local-model", exact_model_tag=MODEL,
            expected_configuration_version=version, purpose=GenerationPurpose.SYNTHESIS,
            system_instruction="sys", user_content="hello", timeout_seconds=5,
        ),
        __import__("backend.app.local_ai.generation_contracts", fromlist=["LocalAIAdvisoryResponse"]).LocalAIAdvisoryResponse,
    )
    assert result.generation_result is not None
    assert result.generation_result.state.value == "succeeded"
    # provider_identity stays the coarse configured "kind" ("ollama"), matching
    # pre-Phase-8C behavior and existing consumers (e.g. chat's slm_provider
    # field) -- not the specific registry key ("ollama-local").
    assert result.generation_result.provider_identity == "ollama"
    assert fake_ollama.generate_calls == 1


def test_generation_works_with_llama_cpp_through_the_registry(tmp_path: Path) -> None:
    configuration = load_local_ai_configuration({
        "ASTRA_LOCAL_AI_GENERATION_ENABLED": "true",
        "ASTRA_LOCAL_AI_PROVIDER": "llama_cpp",
        "ASTRA_LOCAL_AI_MODEL": LLAMA_CPP_MODEL,
        "ASTRA_LLAMA_CPP_ENABLED": "true",
        "ASTRA_LLAMA_CPP_MODEL": LLAMA_CPP_MODEL,
    })
    fake_llama_cpp = _FakeClient(model=LLAMA_CPP_MODEL)
    registry = ProviderRegistry()
    registry.register(LlamaCppProviderAdapter(
        endpoint_identity="http://127.0.0.1:8081", configured_model=LLAMA_CPP_MODEL, client=fake_llama_cpp,
    ))
    database = tmp_path / "phase8c-llama.db"
    apply_schema_migrations(database)
    service = LocalAIService(
        database, configuration=configuration, provider_registry=registry,
        probe=lambda: _capabilities(),
    )
    service.initialize()
    # Manually bind `configured-local-model` to the llama-cpp-local provider
    # for this test -- the default seed always binds it to ollama-local, and
    # this phase deliberately keeps Ollama the shipped default (Part 12).
    with service._connect() as connection:  # noqa: SLF001 -- test-only rebinding
        import json as _json
        row = connection.execute(
            "SELECT profile_json FROM local_ai_models WHERE model_profile_id = ?",
            ("configured-local-model",),
        ).fetchone()
        profile = _json.loads(row["profile_json"])
        profile["provider_id"] = "llama-cpp-local"
        profile["provider_model_id"] = LLAMA_CPP_MODEL
        profile["local_available"] = True
        profile["policy_status"] = "ready"
        profile["enabled"] = True
        connection.execute(
            "UPDATE local_ai_models SET provider_id = ?, enabled = 1, profile_json = ? WHERE model_profile_id = ?",
            ("llama-cpp-local", _json.dumps(profile), "configured-local-model"),
        )

    version = service.configuration_state().configuration_version.model_profiles["configured-local-model"]
    from backend.app.local_ai.generation_contracts import LocalAIAdvisoryResponse

    result = service.execute_structured_generation(
        LocalAIExecutionRequest(
            request_id="r2", idempotency_key="idem-llama-cpp", actor_id="tester",
            model_profile_id="configured-local-model", exact_model_tag=LLAMA_CPP_MODEL,
            expected_configuration_version=version, purpose=GenerationPurpose.SYNTHESIS,
            system_instruction="sys", user_content="hello", timeout_seconds=5,
        ),
        LocalAIAdvisoryResponse,
    )
    assert result.generation_result is not None
    assert result.generation_result.state.value == "succeeded", result.generation_result.user_message
    assert result.generation_result.provider_identity == "llama_cpp"
    assert fake_llama_cpp.generate_calls == 1


def test_missing_provider_fails_closed_with_no_fallback(tmp_path: Path) -> None:
    """A model profile bound to a provider_id the registry doesn't have
    fails explicitly -- it must never silently substitute a different
    registered provider."""
    empty_registry = ProviderRegistry()
    service = _service(tmp_path, provider_registry=empty_registry)
    version = service.configuration_state().configuration_version.model_profiles["configured-local-model"]
    from backend.app.local_ai.generation_contracts import LocalAIAdvisoryResponse

    # enabling requires local_available (which requires the capability probe
    # to say so); force it enabled directly to isolate the generation-time
    # provider-resolution failure from the separate enablement gate.
    with service._connect() as connection:  # noqa: SLF001 -- test-only direct enable
        connection.execute(
            "UPDATE local_ai_models SET enabled = 1, config_version = config_version + 1 "
            "WHERE model_profile_id = 'configured-local-model'",
        )
    version = service.configuration_state().configuration_version.model_profiles["configured-local-model"]

    result = service.execute_structured_generation(
        LocalAIExecutionRequest(
            request_id="r3", idempotency_key="idem-missing-provider", actor_id="tester",
            model_profile_id="configured-local-model", exact_model_tag=MODEL,
            expected_configuration_version=version, purpose=GenerationPurpose.SYNTHESIS,
            system_instruction="sys", user_content="hello", timeout_seconds=5,
        ),
        LocalAIAdvisoryResponse,
    )
    assert result.generation_result is not None
    assert result.generation_result.state.value == "failed"
    assert result.generation_result.failure_reason.value == "provider_not_registered"


def test_capability_refresh_includes_configured_providers_and_never_touches_configuration_version(
    tmp_path: Path,
) -> None:
    configuration = load_local_ai_configuration({
        "ASTRA_LOCAL_AI_GENERATION_ENABLED": "true",
        "ASTRA_LOCAL_AI_MODEL": MODEL,
        "ASTRA_LLAMA_CPP_ENABLED": "true",
        "ASTRA_LLAMA_CPP_MODEL": LLAMA_CPP_MODEL,
    })
    fake_llama_cpp = _FakeClient(model=LLAMA_CPP_MODEL)
    registry = default_provider_registry(configuration)
    # Swap in the fake client for the already-registered llama-cpp-local
    # adapter's transport so the probe never touches the network.
    registry.get("llama-cpp-local")._client = fake_llama_cpp  # noqa: SLF001 -- test-only injection
    database = tmp_path / "phase8c-capabilities.db"
    apply_schema_migrations(database)
    service = LocalAIService(database, configuration=configuration, provider_registry=registry)
    service.initialize()

    before = service.configuration_state().configuration_version.model_profiles["configured-local-model"]
    report = service.capability_report(refresh=True)
    after = service.configuration_state().configuration_version.model_profiles["configured-local-model"]

    assert before == after, "capability refresh must never mutate a model's configuration_version"
    ids = {item.capability_id for item in report.capabilities}
    assert "llama_cpp" in ids
    llama_capability = next(item for item in report.capabilities if item.capability_id == "llama_cpp")
    assert llama_capability.provider_reachable is True


def test_capability_refresh_omits_llama_cpp_when_not_configured(tmp_path: Path) -> None:
    """Purely additive: an operator who never touched llama.cpp sees the
    exact same capability snapshot shape as before this phase."""
    service = _service(tmp_path)
    report = service.capability_report(refresh=True)
    ids = {item.capability_id for item in report.capabilities}
    assert "llama_cpp" not in ids
