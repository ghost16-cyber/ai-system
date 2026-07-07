from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.main import create_app


def test_chat_run_returns_useful_backend_response(tmp_path: Path):
    with TestClient(create_app(tmp_path / "app.db", workspace_root=tmp_path)) as client:
        response = client.post(
            "/chat/run",
            json={
                "message": "Safely check this repo without applying patches.",
                "use_rag": False,
                "safety_mode": "read_only",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["run_id"]
    assert body["conversation_id"]
    assert body["user_message"] == "Safely check this repo without applying patches."
    assert body["assistant_response"]
    assert body["selected_specialist"]
    assert body["intent"]
    assert 0 <= body["confidence"] <= 1
    assert body["rag_used"] is False
    assert body["rag_context_count"] == 0
    assert body["safety_decision"] in {"allow", "downgrade", "block"}
    assert body["runtime_decision"]
    assert body["trace_summary"]
    assert "No files were changed" in body["assistant_response"]


def test_chat_run_uses_rag_context_when_enabled(tmp_path: Path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "phase49.md").write_text(
        "Phase 49 chat workflow should group one readable run per message.",
        encoding="utf-8",
    )

    with TestClient(create_app(tmp_path / "app.db", workspace_root=tmp_path)) as client:
        response = client.post(
            "/chat/run",
            json={
                "message": "What should Phase 49 chat history show?",
                "use_rag": True,
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["rag_used"] is True
    assert body["rag_context_count"] == 1
    assert "phase49.md" in body["assistant_response"]
    rag_trace = next(item for item in body["trace_summary"] if item["phase"] == "rag")
    assert rag_trace["data"]["count"] == 1


def test_chat_run_gracefully_falls_back_when_rag_and_slm_fail(tmp_path: Path, monkeypatch):
    from backend.app import chat_workflow

    def broken_rag(*args, **kwargs):
        raise RuntimeError("index unavailable")

    def broken_slm(*args, **kwargs):
        raise RuntimeError("slm offline")

    monkeypatch.setattr(chat_workflow.rag_context_service, "rag_search", broken_rag)
    monkeypatch.setattr(chat_workflow.slm_gateway, "chat_with_slm", broken_slm)

    with TestClient(create_app(tmp_path / "app.db", workspace_root=tmp_path)) as client:
        response = client.post(
            "/chat/run",
            json={"message": "Plan a RAG workflow", "use_rag": True},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["rag_used"] is False
    assert body["rag_context_count"] == 0
    assert body["assistant_response"]
    titles = [item["title"] for item in body["trace_summary"]]
    assert "RAG unavailable" in titles
    assert "SLM unavailable" in titles


def test_chat_run_stores_one_clear_history_record(tmp_path: Path):
    database_path = tmp_path / "app.db"
    with TestClient(create_app(database_path, workspace_root=tmp_path)) as client:
        response = client.post(
            "/chat/run",
            json={"message": "Explain runtime safety", "use_rag": False},
        )
        runs = client.get("/chat/runs")

    assert response.status_code == 200
    assert runs.status_code == 200
    items = runs.json()["items"]
    assert len(items) == 1
    assert items[0]["run_id"] == response.json()["run_id"]
    assert items[0]["user_message"] == "Explain runtime safety"
    assert items[0]["assistant_response"]
    assert items[0]["selected_specialist"]

    with sqlite3.connect(database_path) as connection:
        stored_count = connection.execute("SELECT COUNT(*) FROM chat_runs").fetchone()[0]
    assert stored_count == 1
