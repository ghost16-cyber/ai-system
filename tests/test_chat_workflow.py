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
    assert body["rag_skip_reason"] == "disabled"
    assert body["rag_context_count"] == 0
    assert body["safety_decision"] in {"allow", "downgrade", "block"}
    assert body["runtime_decision"]
    assert body["used_real_slm"] is False
    assert body["slm_provider"] == "fallback"
    assert body["slm_fallback_reason"] == "ollama_unreachable"
    assert body["memory_used"] is False
    assert body["memory_summary"] is None
    assert body["trace_summary"]
    assert "No files were changed" in body["assistant_response"]


def test_chat_run_creates_new_conversation(tmp_path: Path):
    with TestClient(create_app(tmp_path / "app.db", workspace_root=tmp_path)) as client:
        response = client.post(
            "/chat/run",
            json={"message": "Explain runtime safety", "use_rag": False},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["conversation_id"]
    assert body["memory_used"] is False
    assert body["memory_summary"] is None


def test_chat_run_continues_existing_conversation_with_memory(
    tmp_path: Path,
    monkeypatch,
):
    from backend.app import chat_workflow

    captured_prompt = ""

    def capture_slm(message, context):
        nonlocal captured_prompt
        captured_prompt = context["prompt"]
        return {
            "source": "fallback",
            "provider": "fallback",
            "used_real_slm": False,
            "fallback_reason": "test_fallback",
        }

    monkeypatch.setattr(chat_workflow.slm_gateway, "chat_with_slm", capture_slm)

    first_message = "Safely inspect the backend chat workflow."
    with TestClient(create_app(tmp_path / "app.db", workspace_root=tmp_path)) as client:
        first = client.post(
            "/chat/run",
            json={"message": first_message, "use_rag": False},
        )
        conversation_id = first.json()["conversation_id"]
        second = client.post(
            "/chat/run",
            json={
                "message": "What did I ask first?",
                "use_rag": False,
                "conversation_id": conversation_id,
            },
        )

    assert second.status_code == 200
    body = second.json()
    assert body["conversation_id"] == conversation_id
    assert body["memory_used"] is True
    assert first_message in body["memory_summary"]
    assert first_message in body["assistant_response"]
    assert "Conversation context" in captured_prompt


def test_chat_run_without_conversation_id_starts_fresh_conversation(tmp_path: Path):
    with TestClient(create_app(tmp_path / "app.db", workspace_root=tmp_path)) as client:
        first = client.post("/chat/run", json={"message": "First topic", "use_rag": False})
        second = client.post("/chat/run", json={"message": "Second topic", "use_rag": False})

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["conversation_id"] != second.json()["conversation_id"]
    assert second.json()["memory_used"] is False


def test_chat_conversation_listing_detail_and_deletion(tmp_path: Path):
    database_path = tmp_path / "app.db"
    with TestClient(create_app(database_path, workspace_root=tmp_path)) as client:
        first = client.post("/chat/run", json={"message": "List this conversation", "use_rag": False})
        conversation_id = first.json()["conversation_id"]
        client.post(
            "/chat/run",
            json={
                "message": "Continue that thought",
                "use_rag": False,
                "conversation_id": conversation_id,
            },
        )
        listing = client.get("/chat/conversations")
        detail = client.get(f"/chat/conversations/{conversation_id}")
        deleted = client.delete(f"/chat/conversations/{conversation_id}")
        detail_after_delete = client.get(f"/chat/conversations/{conversation_id}")

    assert listing.status_code == 200
    assert listing.json()["items"][0]["conversation_id"] == conversation_id
    assert listing.json()["items"][0]["turn_count"] == 2
    assert detail.status_code == 200
    assert len(detail.json()["turns"]) == 2
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True
    assert deleted.json()["deleted_turns"] == 2
    assert detail_after_delete.status_code == 404


def test_rag_gating_still_uses_latest_message_with_conversation_memory(
    tmp_path: Path,
):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "phase49.md").write_text(
        "Phase 49 chat workflow should group one readable run per message.",
        encoding="utf-8",
    )

    with TestClient(create_app(tmp_path / "app.db", workspace_root=tmp_path)) as client:
        first = client.post(
            "/chat/run",
            json={"message": "What should Phase 49 chat history show?", "use_rag": True},
        )
        second = client.post(
            "/chat/run",
            json={
                "message": "Can you say that again?",
                "use_rag": True,
                "conversation_id": first.json()["conversation_id"],
            },
        )

    assert first.status_code == 200
    assert first.json()["rag_used"] is True
    assert second.status_code == 200
    body = second.json()
    assert body["memory_used"] is True
    assert body["rag_used"] is False
    assert body["rag_skip_reason"] == "low_relevance"


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


def test_chat_run_skips_rag_for_greeting(tmp_path: Path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "noise.md").write_text("cleanup duplicate file notes", encoding="utf-8")

    with TestClient(create_app(tmp_path / "app.db", workspace_root=tmp_path)) as client:
        response = client.post("/chat/run", json={"message": "hi", "use_rag": True})

    assert response.status_code == 200
    body = response.json()
    assert body["rag_used"] is False
    assert body["rag_context_count"] == 0
    assert "Hi." in body["assistant_response"]
    rag_trace = next(item for item in body["trace_summary"] if item["phase"] == "rag")
    assert rag_trace["data"]["reason"] == "greeting"


def test_chat_run_skips_rag_for_astra_capability_question(tmp_path: Path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "cleanup.md").write_text(
        "Cleanup duplicate files and stale generated folders.",
        encoding="utf-8",
    )

    with TestClient(create_app(tmp_path / "app.db", workspace_root=tmp_path)) as client:
        response = client.post(
            "/chat/run",
            json={
                "message": "Explain what this Astra system can currently do in 5 simple bullet points",
                "use_rag": True,
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["rag_used"] is False
    assert body["rag_context_count"] == 0
    assert "local prototype assistant" in body["assistant_response"]
    assert "duplicate files" not in body["assistant_response"].lower()
    assert body["assistant_response"].count("\n- ") == 4
    rag_trace = next(item for item in body["trace_summary"] if item["phase"] == "rag")
    assert rag_trace["data"]["reason"] == "system_meta_question"


def test_irrelevant_rag_context_is_not_injected_into_slm_prompt(
    tmp_path: Path,
    monkeypatch,
):
    from backend.app import chat_workflow

    captured_prompt = ""

    def unrelated_rag(*args, **kwargs):
        return {
            "results": [
                {
                    "path": "docs/cleanup.md",
                    "title": "cleanup.md",
                    "snippet": "Duplicate file cleanup notes for old artifacts.",
                    "score": 1.0,
                }
            ]
        }

    def capture_slm(message, context):
        nonlocal captured_prompt
        captured_prompt = context["prompt"]
        return {
            "source": "local_slm",
            "provider": "ollama",
            "model": "qwen2.5-coder:1.5b",
            "used_real_slm": True,
            "fallback_reason": None,
            "latency_ms": 5,
            "assistant_response": "Use the backend test suite and inspect failing assertions.",
        }

    monkeypatch.setattr(chat_workflow.rag_context_service, "rag_search", unrelated_rag)
    monkeypatch.setattr(chat_workflow.slm_gateway, "chat_with_slm", capture_slm)

    with TestClient(create_app(tmp_path / "app.db", workspace_root=tmp_path)) as client:
        response = client.post(
            "/chat/run",
            json={"message": "How do I fix backend tests?", "use_rag": True},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["rag_used"] is False
    assert body["rag_context_count"] == 0
    assert "Duplicate file cleanup" not in captured_prompt
    assert "No RAG context is attached" in captured_prompt
    rag_trace = next(item for item in body["trace_summary"] if item["phase"] == "rag")
    assert rag_trace["data"]["reason"] == "low_relevance"


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
    assert body["used_real_slm"] is False
    assert body["slm_provider"] == "fallback"
    assert body["slm_fallback_reason"].startswith("gateway_exception:")


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


def test_chat_run_includes_slm_metadata_when_real_slm_used(tmp_path: Path, monkeypatch):
    from backend.app import chat_workflow

    def mock_chat_with_slm(*args, **kwargs):
        return {
            "source": "local_slm",
            "provider": "ollama",
            "model": "qwen2.5-coder:1.5b",
            "used_real_slm": True,
            "fallback_reason": None,
            "latency_ms": 12,
            "assistant_response": "Real response text",
        }

    monkeypatch.setattr(chat_workflow.slm_gateway, "chat_with_slm", mock_chat_with_slm)

    with TestClient(create_app(tmp_path / "app.db", workspace_root=tmp_path)) as client:
        response = client.post("/chat/run", json={"message": "Test SLM inclusion"})

    assert response.status_code == 200
    body = response.json()
    assert body["used_real_slm"] is True
    assert body["slm_provider"] == "ollama"
    assert body["slm_model"] == "qwen2.5-coder:1.5b"
    assert body["slm_fallback_reason"] is None
    assert body["slm_latency_ms"] == 12
    assert "Real response text" in body["assistant_response"]
    slm_trace = next(item for item in body["trace_summary"] if item["phase"] == "slm")
    assert slm_trace["title"] == "SLM response generated"
    assert slm_trace["data"]["used_real_slm"] is True
