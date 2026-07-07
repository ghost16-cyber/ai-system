from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.slm.gateway import infer_intent_with_slm


def test_slm_runtime_profiles_and_selection(tmp_path: Path):
    with TestClient(create_app(tmp_path / "app.db", workspace_root=tmp_path)) as client:
        profiles = client.get("/runtime/slm/profiles")
        selected = client.get("/runtime/slm/selected")
        valid = client.post(
            "/runtime/slm/select",
            json={"profile_id": profiles.json()["profiles"][0]["profile_id"]},
        )
        invalid = client.post("/runtime/slm/select", json={"profile_id": "missing"})

    assert profiles.status_code == 200
    assert profiles.json()["count"] >= 3
    assert selected.status_code == 200
    assert selected.json()["loaded"] is False
    assert valid.status_code == 200
    assert valid.json()["model_loaded"] is False
    assert valid.json()["tools_authorized"] is False
    assert invalid.status_code == 400


def test_slm_chat_and_intent_are_safe(tmp_path: Path):
    with TestClient(create_app(tmp_path / "app.db", workspace_root=tmp_path)) as client:
        chat = client.post("/slm/chat", json={"message": "Help me plan a RAG index"})
        intent = client.post("/slm/intent", json={"message": "CUDA runtime error"})

    assert chat.status_code == 200
    assert chat.json()["source"] == "mock"
    assert chat.json()["advisory_only"] is True
    assert chat.json()["tools_executed"] is False
    assert chat.json()["patches_applied"] is False
    assert chat.json()["runtime_authorized"] is False
    assert intent.status_code == 200
    assert isinstance(intent.json()["task_type"], str)
    assert intent.json()["intent"]
    assert intent.json()["runtime_authorized"] is False


def test_invalid_slm_intent_output_falls_back_safely():
    intent = infer_intent_with_slm(
        "Build a RAG retrieval index",
        raw_model_output="not json",
    )

    assert intent["task_type"] == "rag"
    assert intent["source"] == "mock"
    assert intent["tools_executed"] is False


def test_specialist_route_with_slm_intent_is_advisory_only(tmp_path: Path):
    with TestClient(create_app(tmp_path / "app.db", workspace_root=tmp_path)) as client:
        routed = client.post(
            "/specialists/route",
            json={"text": "security token leaked", "use_slm_intent": True},
        )
        traces = client.get("/specialists/traces")

    assert routed.status_code == 200
    body = routed.json()
    assert body["recommended_specialist"] == "safety_specialist"
    assert body["slm_intent_used"] is True
    assert body["deterministic_decision"] == body["final_decision"]
    assert body["execution_allowed"] is False
    assert any(trace.get("slm_intent_used") is True for trace in traces.json()["traces"])


def test_router_benchmark_remains_deterministic_by_default(tmp_path: Path):
    with TestClient(create_app(tmp_path / "app.db", workspace_root=tmp_path)) as client:
        benchmark = client.get("/specialists/router/benchmark")

    assert benchmark.status_code == 200
    assert benchmark.json()["overall_accuracy"] == 1.0


def test_rag_status_search_and_context_endpoints_are_safe(tmp_path: Path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "guide.md").write_text("Specialist router benchmark and fallback notes.", encoding="utf-8")
    (tmp_path / ".env").write_text("TOKEN=should-not-appear", encoding="utf-8")

    with TestClient(create_app(tmp_path / "app.db", workspace_root=tmp_path)) as client:
        status = client.get("/rag/status")
        search = client.post("/rag/search", json={"query": "specialist fallback", "limit": 5})
        chat = client.post("/slm/chat-with-context", json={"message": "specialist fallback"})
        routed = client.post("/specialists/route-with-context", json={"text": "specialist fallback"})

    assert status.status_code == 200
    assert status.json()["tools_executed"] is False
    assert search.status_code == 200
    assert search.json()["results"]
    assert "should-not-appear" not in str(search.json())
    assert chat.status_code == 200
    assert chat.json()["advisory_only"] is True
    assert routed.status_code == 200
    assert routed.json()["advisory_only"] is True
    assert routed.json()["execution_allowed"] is False


def test_rag_search_handles_empty_index_safely(tmp_path: Path):
    with TestClient(create_app(tmp_path / "app.db", workspace_root=tmp_path)) as client:
        search = client.post("/rag/search", json={"query": "nothing"})

    assert search.status_code == 200
    assert search.json()["results"] == []
    assert search.json()["runtime_authorized"] is False
