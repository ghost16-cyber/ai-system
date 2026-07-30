from __future__ import annotations

import pytest

from backend.app.local_ai.providers import (
    DuplicateProviderError,
    FakeDeterministicProvider,
    OllamaProviderAdapter,
    ProviderNotRegisteredError,
    ProviderRegistry,
)
from backend.app.local_ai.contracts import ModelProfile


def _model_profile(**overrides) -> ModelProfile:
    values = dict(
        model_profile_id="configured-local-model",
        provider_id="ollama-local",
        provider_model_id="qwen2.5-coder:1.5b",
        display_name="Configured local model",
        model_family="qwen2.5-coder",
        parameter_scale="1.5b",
        context_window=32768,
        operational_context=4096,
        maximum_output_tokens=2048,
        estimated_model_bytes=0,
        minimum_ram_bytes=0,
        minimum_vram_bytes=0,
        prompt_template_profile="configured-local-coder-v1",
    )
    values.update(overrides)
    return ModelProfile(**values)


def test_registration_and_lookup_by_provider_id() -> None:
    registry = ProviderRegistry()
    ollama = OllamaProviderAdapter(endpoint_identity="http://127.0.0.1:11434")
    registry.register(ollama)
    assert registry.get("ollama-local") is ollama
    assert registry.get_or_none("ollama-local") is ollama
    assert registry.get_or_none("does-not-exist") is None


def test_duplicate_provider_id_is_rejected() -> None:
    registry = ProviderRegistry()
    registry.register(OllamaProviderAdapter(endpoint_identity="http://127.0.0.1:11434"))
    with pytest.raises(DuplicateProviderError):
        registry.register(OllamaProviderAdapter(endpoint_identity="http://127.0.0.1:9999"))


def test_missing_provider_raises_explicit_not_registered_error() -> None:
    registry = ProviderRegistry()
    with pytest.raises(ProviderNotRegisteredError) as excinfo:
        registry.get("llama-cpp-local")
    assert excinfo.value.provider_id == "llama-cpp-local"
    assert str(excinfo.value) == "provider_not_registered"


def test_listing_is_deterministic_registration_order() -> None:
    registry = ProviderRegistry()
    registry.register(FakeDeterministicProvider(provider_id="fake-deterministic"))
    registry.register(OllamaProviderAdapter(endpoint_identity="http://127.0.0.1:11434"))
    assert registry.list_provider_ids() == ("fake-deterministic", "ollama-local")
    # Repeated calls are stable, not re-sorted or randomized.
    assert registry.list_provider_ids() == ("fake-deterministic", "ollama-local")


def test_resolve_for_model_follows_provider_id_with_no_fallback() -> None:
    registry = ProviderRegistry()
    ollama = OllamaProviderAdapter(endpoint_identity="http://127.0.0.1:11434")
    registry.register(ollama)
    profile = _model_profile(provider_id="ollama-local")
    assert registry.resolve_for_model(profile) is ollama

    unresolvable = _model_profile(provider_id="llama-cpp-local")
    with pytest.raises(ProviderNotRegisteredError):
        registry.resolve_for_model(unresolvable)
