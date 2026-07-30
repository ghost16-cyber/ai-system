from __future__ import annotations

import json

from backend.app.local_ai.config import load_local_ai_configuration
from backend.app.local_ai.contracts import CapabilityStatus, OllamaCapability
from backend.app.local_ai.provider import ProviderClientError, ProviderErrorCode, ProviderGenerationRequest
from backend.app.local_ai.providers.ollama import OllamaProviderAdapter
from backend.app.local_ai.provider import url_request


class _Response:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, _maximum):
        return self._body


def test_capability_declaration_is_accurate_for_ollama() -> None:
    adapter = OllamaProviderAdapter(endpoint_identity="http://127.0.0.1:11434")
    assert adapter.provider_id == "ollama-local"
    assert adapter.capabilities.generation_supported is True
    assert adapter.capabilities.structured_output_supported is True
    assert adapter.capabilities.model_discovery_supported is True
    assert adapter.capabilities.loaded_model_discovery_supported is True
    # Ollama has no mid-request cancellation primitive this client uses.
    assert adapter.capabilities.cancellation_supported is False


def test_inspect_and_generate_delegate_to_the_real_ollama_client(monkeypatch) -> None:
    """The adapter must never reimplement HTTP transport -- it delegates to
    `OllamaProviderClient`, so class-level monkeypatches on that class (used
    by the Phase 5B smoke-test) keep working through this adapter too."""
    def fake_urlopen(request, timeout=None):
        del timeout
        if request.data is None:
            return _Response(json.dumps({"models": [{"name": "qwen2.5-coder:1.5b"}]}).encode())
        return _Response(json.dumps({
            "model": "qwen2.5-coder:1.5b", "response": "hello",
            "prompt_eval_count": 10, "eval_count": 5,
        }).encode())

    monkeypatch.setattr(url_request, "urlopen", fake_urlopen)
    adapter = OllamaProviderAdapter(endpoint_identity="http://127.0.0.1:11434")

    inspection = adapter.inspect(timeout_seconds=2)
    assert inspection.installed_models == ("qwen2.5-coder:1.5b",)

    response = adapter.generate(ProviderGenerationRequest(
        model="qwen2.5-coder:1.5b", system_instruction="sys", prompt="hi",
        timeout_seconds=5, maximum_output_tokens=32, structured_json=False,
    ))
    assert response.response == "hello"
    assert response.metadata["prompt_eval_count"] == 10


def test_probe_capability_reports_unavailable_on_provider_unreachable(monkeypatch) -> None:
    def raise_unreachable(*_args, **_kwargs):
        raise ProviderClientError(ProviderErrorCode.UNREACHABLE, "unreachable")

    monkeypatch.setattr(url_request, "urlopen", raise_unreachable)
    adapter = OllamaProviderAdapter(endpoint_identity="http://127.0.0.1:11434")
    capability = adapter.probe_capability(load_local_ai_configuration({
        "ASTRA_LOCAL_AI_MODEL": "qwen2.5-coder:1.5b",
    }))
    assert isinstance(capability, OllamaCapability)
    assert capability.status == CapabilityStatus.UNAVAILABLE
    assert capability.provider_reachable is False
    assert capability.reason == "provider_unreachable"


def test_probe_capability_reports_configured_model_missing_accurately(monkeypatch) -> None:
    def fake_urlopen(request, timeout=None):
        del request, timeout
        return _Response(json.dumps({"models": [{"name": "some-other-model:1b"}]}).encode())

    monkeypatch.setattr(url_request, "urlopen", fake_urlopen)
    adapter = OllamaProviderAdapter(endpoint_identity="http://127.0.0.1:11434")
    capability = adapter.probe_capability(load_local_ai_configuration({
        "ASTRA_LOCAL_AI_MODEL": "qwen2.5-coder:1.5b",
    }))
    assert capability.configured_model_missing is True
    assert capability.status == CapabilityStatus.UNAVAILABLE
    assert capability.installed_models == ("some-other-model:1b",)
