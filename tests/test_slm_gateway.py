from __future__ import annotations

from backend.app.slm.gateway import chat_with_slm, get_slm_gateway_status


def test_chat_gateway_uses_real_slm_when_available(monkeypatch):
    prompts: list[str] = []
    build_kwargs: dict[str, object] = {}

    monkeypatch.setenv("ASTRA_OLLAMA_BASE_URL", "http://fake")
    monkeypatch.setattr(
        "backend.app.slm.gateway._check_ollama_reachable",
        lambda *args, **kwargs: (True, ["qwen2.5-coder:1.5b"]),
    )

    class FakeClient:
        def generate(self, prompt: str) -> str:
            prompts.append(prompt)
            return "Real SLM Output"

    def fake_build_ollama_client(**kwargs):
        build_kwargs.update(kwargs)
        return FakeClient()

    monkeypatch.setattr(
        "backend.app.slm.gateway.build_ollama_client",
        fake_build_ollama_client,
    )

    result = chat_with_slm("Testing", {"selected_specialist": "code"})

    assert result["used_real_slm"] is True
    assert result["assistant_response"] == "Real SLM Output"
    assert result["provider"] == "ollama"
    assert result["model"] == "qwen2.5-coder:1.5b"
    assert result["fallback_reason"] is None
    assert isinstance(result["latency_ms"], int)
    assert build_kwargs["base_url"] == "http://fake"
    assert build_kwargs["timeout_seconds"] == 30
    assert "selected_specialist" in prompts[0]


def test_chat_gateway_fallback_when_unreachable(monkeypatch):
    monkeypatch.setattr(
        "backend.app.slm.gateway._check_ollama_reachable",
        lambda *args, **kwargs: (False, []),
    )

    result = chat_with_slm("Testing")

    assert result["used_real_slm"] is False
    assert result["provider"] == "fallback"
    assert result["fallback_reason"] == "ollama_unreachable"
    assert result["backend_available"] is False


def test_chat_gateway_fallback_when_model_missing(monkeypatch):
    monkeypatch.setattr(
        "backend.app.slm.gateway._check_ollama_reachable",
        lambda *args, **kwargs: (True, ["other-model:latest"]),
    )

    result = chat_with_slm("Testing")

    assert result["used_real_slm"] is False
    assert result["fallback_reason"] == "model_missing"
    assert result["model"] == "qwen2.5-coder:1.5b"


def test_chat_gateway_fallback_when_disabled(monkeypatch):
    monkeypatch.setenv("ASTRA_SLM_ENABLED", "false")
    monkeypatch.setattr(
        "backend.app.slm.gateway._check_ollama_reachable",
        lambda *args, **kwargs: (True, ["qwen2.5-coder:1.5b"]),
    )

    result = chat_with_slm("Testing")

    assert result["used_real_slm"] is False
    assert result["fallback_reason"] == "disabled_by_config"


def test_chat_gateway_fallback_on_timeout(monkeypatch):
    monkeypatch.setattr(
        "backend.app.slm.gateway._check_ollama_reachable",
        lambda *args, **kwargs: (True, ["qwen2.5-coder:1.5b"]),
    )

    class TimeoutClient:
        def generate(self, prompt: str) -> str:
            raise TimeoutError("too slow")

    monkeypatch.setattr(
        "backend.app.slm.gateway.build_ollama_client",
        lambda **kwargs: TimeoutClient(),
    )

    result = chat_with_slm("Testing")

    assert result["used_real_slm"] is False
    assert result["fallback_reason"] == "timeout"


def test_chat_gateway_fallback_on_invalid_response(monkeypatch):
    monkeypatch.setattr(
        "backend.app.slm.gateway._check_ollama_reachable",
        lambda *args, **kwargs: (True, ["qwen2.5-coder:1.5b"]),
    )

    class EmptyClient:
        def generate(self, prompt: str) -> str:
            return ""

    monkeypatch.setattr(
        "backend.app.slm.gateway.build_ollama_client",
        lambda **kwargs: EmptyClient(),
    )

    result = chat_with_slm("Testing")

    assert result["used_real_slm"] is False
    assert result["fallback_reason"] == "invalid_response"


def test_slm_gateway_status_reports_selected_model(monkeypatch):
    monkeypatch.setenv("ASTRA_SLM_MODEL", "custom:latest")
    monkeypatch.setattr(
        "backend.app.slm.gateway._check_ollama_reachable",
        lambda *args, **kwargs: (True, ["custom:latest"]),
    )

    status = get_slm_gateway_status()

    assert status["enabled"] is True
    assert status["provider"] == "ollama"
    assert status["configured_model"] == "custom:latest"
    assert status["selected_model"] == "custom:latest"
    assert status["reachable"] is True
