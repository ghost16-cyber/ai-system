from __future__ import annotations

import json

import pytest

from backend.app.local_ai.config import load_local_ai_configuration
from backend.app.local_ai.contracts import CapabilityStatus, LlamaCppCapability
from backend.app.local_ai.provider import ProviderClientError, ProviderErrorCode, ProviderGenerationRequest
from backend.app.local_ai.providers.llama_cpp import LlamaCppProviderAdapter, url_request


def _generation_request(**overrides) -> ProviderGenerationRequest:
    values = dict(
        model="qwen-gguf",
        system_instruction="sys",
        prompt="hello",
        timeout_seconds=5,
        maximum_output_tokens=32,
        structured_json=False,
    )
    values.update(overrides)
    return ProviderGenerationRequest(**values)


class _Response:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, _maximum):
        return self._body


def _chat_completion(content: str, *, finish_reason: str = "stop", model: str = "qwen-gguf") -> dict:
    return {
        "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": finish_reason}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
    }


def test_health_probe_reports_reachable_and_loaded_model(monkeypatch) -> None:
    def fake_urlopen(request, timeout=None):
        del timeout
        assert request.data is None
        return _Response(json.dumps({"object": "list", "data": [{"id": "qwen-gguf"}]}).encode())

    monkeypatch.setattr(url_request, "urlopen", fake_urlopen)
    adapter = LlamaCppProviderAdapter(endpoint_identity="http://127.0.0.1:8081", configured_model="qwen-gguf")
    capability = adapter.probe_capability(load_local_ai_configuration({}))
    assert capability.status == CapabilityStatus.AVAILABLE
    assert capability.provider_reachable is True
    assert capability.configured_model_missing is False
    assert capability.loaded_models == ("qwen-gguf",)
    assert capability.runtime_kind == "llama.cpp"


def test_configured_model_identity_mismatch_is_reported_accurately(monkeypatch) -> None:
    def fake_urlopen(request, timeout=None):
        del timeout
        return _Response(json.dumps({"object": "list", "data": [{"id": "a-different-model"}]}).encode())

    monkeypatch.setattr(url_request, "urlopen", fake_urlopen)
    adapter = LlamaCppProviderAdapter(endpoint_identity="http://127.0.0.1:8081", configured_model="qwen-gguf")
    capability = adapter.probe_capability(load_local_ai_configuration({}))
    assert capability.configured_model_missing is True
    assert capability.status == CapabilityStatus.UNAVAILABLE
    assert capability.reason == "configured_model_not_loaded"


def test_openai_compatible_generation_normalizes_usage(monkeypatch) -> None:
    def fake_urlopen(request, timeout=None):
        del timeout
        if request.data is None:
            return _Response(json.dumps({"data": [{"id": "qwen-gguf"}]}).encode())
        payload = json.loads(request.data.decode("utf-8"))
        assert payload["messages"][0]["role"] == "system"
        assert payload["messages"][1]["content"] == "hello"
        return _Response(json.dumps(_chat_completion('{"answer": 42}')).encode())

    monkeypatch.setattr(url_request, "urlopen", fake_urlopen)
    adapter = LlamaCppProviderAdapter(endpoint_identity="http://127.0.0.1:8081")
    response = adapter.generate(_generation_request())
    assert response.model == "qwen-gguf"
    assert response.response == '{"answer": 42}'
    assert response.metadata == {"prompt_eval_count": 5, "eval_count": 3}


def test_structured_output_requests_json_object_response_format(monkeypatch) -> None:
    captured = {}

    def fake_urlopen(request, timeout=None):
        del timeout
        if request.data is None:
            return _Response(json.dumps({"data": []}).encode())
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return _Response(json.dumps(_chat_completion("{}")).encode())

    monkeypatch.setattr(url_request, "urlopen", fake_urlopen)
    adapter = LlamaCppProviderAdapter(endpoint_identity="http://127.0.0.1:8081")
    adapter.generate(_generation_request(structured_json=True))
    assert captured["payload"]["response_format"] == {"type": "json_object"}


def test_varying_finish_reason_does_not_crash_or_leak_into_the_result(monkeypatch) -> None:
    """Neither `ProviderGenerationResponse` nor `LocalGenerationResult` has a
    first-class finish_reason field today (Ollama's own adapter doesn't
    surface `done_reason` either) -- normalization here means the adapter
    tolerates any finish_reason value while extracting content, not that a
    new field is invented to carry it."""
    def fake_urlopen(request, timeout=None):
        del timeout
        if request.data is None:
            return _Response(json.dumps({"data": []}).encode())
        return _Response(json.dumps(_chat_completion("truncated output", finish_reason="length")).encode())

    monkeypatch.setattr(url_request, "urlopen", fake_urlopen)
    adapter = LlamaCppProviderAdapter(endpoint_identity="http://127.0.0.1:8081")
    response = adapter.generate(_generation_request())
    assert response.response == "truncated output"


def test_provider_unreachable_is_reported_explicitly(monkeypatch) -> None:
    import socket
    from urllib import error as url_error

    def fake_urlopen(request, timeout=None):
        del request, timeout
        raise url_error.URLError(OSError("connection refused"))

    monkeypatch.setattr(url_request, "urlopen", fake_urlopen)
    adapter = LlamaCppProviderAdapter(endpoint_identity="http://127.0.0.1:8081")
    capability = adapter.probe_capability(load_local_ai_configuration({}))
    assert capability.status == CapabilityStatus.UNAVAILABLE
    assert capability.provider_reachable is False
    assert capability.reason == "provider_unreachable"

    with pytest.raises(ProviderClientError) as excinfo:
        adapter.generate(_generation_request())
    assert excinfo.value.code == ProviderErrorCode.UNREACHABLE


def test_malformed_response_is_rejected(monkeypatch) -> None:
    def fake_urlopen(request, timeout=None):
        del timeout
        if request.data is None:
            return _Response(b"not json")
        return _Response(json.dumps({"model": "qwen-gguf"}).encode())  # missing "choices"

    monkeypatch.setattr(url_request, "urlopen", fake_urlopen)
    adapter = LlamaCppProviderAdapter(endpoint_identity="http://127.0.0.1:8081")

    with pytest.raises(ProviderClientError) as excinfo:
        adapter.inspect(timeout_seconds=2)
    assert excinfo.value.code == ProviderErrorCode.MALFORMED_RESPONSE

    with pytest.raises(ProviderClientError) as excinfo2:
        adapter.generate(_generation_request())
    assert excinfo2.value.code == ProviderErrorCode.MALFORMED_RESPONSE


def test_timeout_is_reported_explicitly(monkeypatch) -> None:
    def fake_urlopen(request, timeout=None):
        del request, timeout
        raise TimeoutError("timed out")

    monkeypatch.setattr(url_request, "urlopen", fake_urlopen)
    adapter = LlamaCppProviderAdapter(endpoint_identity="http://127.0.0.1:8081")
    with pytest.raises(ProviderClientError) as excinfo:
        adapter.generate(_generation_request())
    assert excinfo.value.code == ProviderErrorCode.TIMEOUT


def test_installed_model_discovery_is_not_claimed(monkeypatch) -> None:
    """llama-server has no "installed but not loaded" registry -- unlike
    Ollama's adapter, installed_models is never invented, only ever empty,
    and the capability declaration says so explicitly."""
    def fake_urlopen(request, timeout=None):
        del timeout
        return _Response(json.dumps({"data": [{"id": "qwen-gguf"}]}).encode())

    monkeypatch.setattr(url_request, "urlopen", fake_urlopen)
    adapter = LlamaCppProviderAdapter(endpoint_identity="http://127.0.0.1:8081")
    assert adapter.capabilities.model_discovery_supported is False
    assert adapter.capabilities.loaded_model_discovery_supported is True
    inspection = adapter.inspect(timeout_seconds=2)
    assert inspection.installed_models == ()
    assert inspection.loaded_models == ("qwen-gguf",)


def test_cancellation_is_advertised_as_unsupported_and_honored_before_the_call() -> None:
    """llama-server's OpenAI-compatible HTTP API has no mid-request
    cancellation primitive this adapter uses -- `cancellation_supported`
    is accurately False, but a caller-side cancellation check still
    prevents the HTTP call from ever being made (the same
    caller-cancellation semantics `OllamaProviderClient` already has)."""
    adapter = LlamaCppProviderAdapter(endpoint_identity="http://127.0.0.1:8081")
    assert adapter.capabilities.cancellation_supported is False

    with pytest.raises(ProviderClientError) as excinfo:
        adapter.generate(_generation_request(), cancelled=lambda: True)
    assert excinfo.value.code == ProviderErrorCode.CANCELLED
