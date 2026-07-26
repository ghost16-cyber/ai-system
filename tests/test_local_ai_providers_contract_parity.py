from __future__ import annotations

import json

import pytest

from backend.app.local_ai.config import load_local_ai_configuration
from backend.app.local_ai.contracts import LlamaCppCapability, OllamaCapability
from backend.app.local_ai.provider import ProviderGenerationRequest
from backend.app.local_ai.providers import (
    FakeDeterministicProvider,
    LlamaCppProviderAdapter,
    OllamaProviderAdapter,
)
from backend.app.local_ai.providers.base import ProviderCapabilityDeclaration


def _generation_request(model: str) -> ProviderGenerationRequest:
    return ProviderGenerationRequest(
        model=model,
        system_instruction="You are a bounded assistant.",
        prompt="Say hello.",
        timeout_seconds=5,
        maximum_output_tokens=64,
        structured_json=False,
    )


ADAPTER_FACTORIES = {
    "fake-deterministic": lambda: FakeDeterministicProvider(),
    "ollama-local": lambda: OllamaProviderAdapter(endpoint_identity="http://127.0.0.1:11434"),
    "llama-cpp-local": lambda: LlamaCppProviderAdapter(endpoint_identity="http://127.0.0.1:8081"),
}


@pytest.mark.parametrize("factory", ADAPTER_FACTORIES.values(), ids=ADAPTER_FACTORIES.keys())
def test_adapter_exposes_the_canonical_provider_surface(factory) -> None:
    adapter = factory()
    assert isinstance(adapter.provider_id, str) and adapter.provider_id
    assert isinstance(adapter.capabilities, ProviderCapabilityDeclaration)
    assert hasattr(adapter, "inspect")
    assert hasattr(adapter, "generate")
    assert hasattr(adapter, "probe_capability")


def test_fake_provider_satisfies_the_contract_end_to_end() -> None:
    adapter = FakeDeterministicProvider()
    inspection = adapter.inspect(timeout_seconds=1)
    assert adapter.provider_id in inspection.installed_models or inspection.installed_models

    response = adapter.generate(_generation_request(inspection.installed_models[0]))
    assert response.model == inspection.installed_models[0]
    assert json.loads(response.response) is not None

    capability = adapter.probe_capability(load_local_ai_configuration({}))
    assert capability.status.value == "available"


def test_ollama_adapter_satisfies_the_contract_with_mocked_http(monkeypatch) -> None:
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

    def fake_urlopen(request, timeout=None):
        del timeout
        if request.data is None:
            return _Response(json.dumps({"models": [{"name": "qwen2.5-coder:1.5b"}]}).encode())
        return _Response(json.dumps({
            "model": "qwen2.5-coder:1.5b", "response": '{"ok": true}',
            "prompt_eval_count": 3, "eval_count": 2,
        }).encode())

    monkeypatch.setattr(url_request, "urlopen", fake_urlopen)
    adapter = OllamaProviderAdapter(endpoint_identity="http://127.0.0.1:11434")

    inspection = adapter.inspect(timeout_seconds=2)
    assert inspection.installed_models == ("qwen2.5-coder:1.5b",)

    response = adapter.generate(_generation_request("qwen2.5-coder:1.5b"))
    assert response.model == "qwen2.5-coder:1.5b"
    assert response.metadata["prompt_eval_count"] == 3

    capability = adapter.probe_capability(load_local_ai_configuration({
        "ASTRA_LOCAL_AI_MODEL": "qwen2.5-coder:1.5b",
    }))
    assert isinstance(capability, OllamaCapability)
    assert capability.provider_reachable is True


def test_llama_cpp_adapter_satisfies_the_contract_with_mocked_http(monkeypatch) -> None:
    from backend.app.local_ai.providers import llama_cpp as llama_cpp_module

    class _Response:
        def __init__(self, body: bytes) -> None:
            self._body = body

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _maximum):
            return self._body

    def fake_urlopen(request, timeout=None):
        del timeout
        if request.data is None:
            return _Response(json.dumps({"object": "list", "data": [{"id": "qwen-gguf"}]}).encode())
        return _Response(json.dumps({
            "model": "qwen-gguf",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": '{"ok": true}'}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6},
        }).encode())

    monkeypatch.setattr(llama_cpp_module.url_request, "urlopen", fake_urlopen)
    adapter = LlamaCppProviderAdapter(endpoint_identity="http://127.0.0.1:8081", configured_model="qwen-gguf")

    inspection = adapter.inspect(timeout_seconds=2)
    assert inspection.installed_models == ()
    assert inspection.loaded_models == ("qwen-gguf",)

    response = adapter.generate(_generation_request("qwen-gguf"))
    assert response.model == "qwen-gguf"
    assert response.metadata == {"prompt_eval_count": 4, "eval_count": 2}

    capability = adapter.probe_capability(load_local_ai_configuration({}))
    assert isinstance(capability, LlamaCppCapability)
    assert capability.provider_reachable is True
    assert capability.configured_model_missing is False
