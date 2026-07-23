from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.runtime.contracts import RuntimeReadiness, RuntimeState


def _client(tmp_path: Path) -> TestClient:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return TestClient(create_app(tmp_path / "app.db", workspace_root=workspace))


def test_chat_reports_runtime_ready_fields_when_healthy(tmp_path: Path) -> None:
    client = _client(tmp_path)
    with client:
        response = client.post("/chat/run", json={"message": "hello", "use_rag": False})
    assert response.status_code == 200
    body = response.json()
    assert body["runtime_state"] == "ready"
    assert body["runtime_ready"] is True
    assert body["runtime_blocking_reasons"] == []


def test_chat_degrades_gracefully_and_still_answers_when_runtime_not_ready(
    tmp_path: Path,
) -> None:
    """Category: chat integration (readiness-gate only). Chat must never be
    blocked by runtime degradation -- it still answers, using the exact same
    retrieval/generation flow, and only reports reduced capability."""
    client = _client(tmp_path)
    with client:
        app = client.app

        def _degraded_readiness(*, project_id=None):
            return RuntimeReadiness(
                ready=False,
                state=RuntimeState.DEGRADED,
                blocking_reasons=("providers_not_healthy",),
                control_ready=True,
                retrieval_ready=True,
                providers_healthy=False,
                pending_recovery=False,
                generated_at=__import__("datetime").datetime.now(
                    __import__("datetime").timezone.utc
                ),
            )

        app.state.runtime_manager.readiness = _degraded_readiness
        response = client.post("/chat/run", json={"message": "hello", "use_rag": False})

    assert response.status_code == 200
    body = response.json()
    assert body["assistant_response"]  # chat still answered
    assert body["runtime_ready"] is False
    assert body["runtime_state"] == "degraded"
    assert "providers_not_healthy" in body["runtime_blocking_reasons"]
    trace_phases = {item["phase"] for item in body["trace_summary"]}
    assert "runtime_degraded" in trace_phases


def test_retrieval_and_generation_flow_is_unchanged_by_runtime_wiring(
    tmp_path: Path,
) -> None:
    """The readiness-gate hook must not alter what chat actually does: RAG
    usage/skip-reason and corpus retrieval fields behave exactly as before
    Phase 8, regardless of runtime state."""
    client = _client(tmp_path)
    with client:
        response = client.post("/chat/run", json={"message": "hello", "use_rag": True})
    assert response.status_code == 200
    body = response.json()
    assert "rag_used" in body
    assert "rag_skip_reason" in body
    assert "corpus_retrieval_used" in body
