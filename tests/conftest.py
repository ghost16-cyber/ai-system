from __future__ import annotations
import pytest

@pytest.fixture(autouse=True)
def _default_ollama_unreachable(monkeypatch):
    """
    Ensure all tests run hermetically regardless of whether a real Ollama is running,
    by default forcing reachability to False for the SLM gateway.
    Tests that specifically need to test real SLM behavior can override this.
    """
    import backend.app.slm.gateway as gateway
    monkeypatch.setattr(gateway, "_check_ollama_reachable", lambda *a, **kw: (False, []))