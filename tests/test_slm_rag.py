from __future__ import annotations

import json
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
    assert search.json()["status"] == "index_missing"
    assert search.json()["runtime_authorized"] is False


def test_project_rag_index_creation_and_status(tmp_path: Path):
    (tmp_path / "app.py").write_text("def project_answer():\n    return 'rag indexing'\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("Project RAG indexing notes.", encoding="utf-8")

    with TestClient(create_app(tmp_path / "app.db", workspace_root=tmp_path)) as client:
        created = client.post("/rag/index")
        status = client.get("/rag/index/status")

    assert created.status_code == 200
    body = created.json()
    assert body["indexed_files"] == 2
    assert body["indexed_chunks"] == 2
    assert body["root"] == str(tmp_path.resolve())
    assert (tmp_path / "data" / "rag" / "project_index.json").exists()
    assert status.status_code == 200
    assert status.json()["exists"] is True
    assert status.json()["indexed_files"] == 2


def test_project_rag_index_uses_astra_project_root_env(tmp_path: Path, monkeypatch):
    project_root = tmp_path / "project"
    app_root = tmp_path / "app"
    project_root.mkdir()
    app_root.mkdir()
    (project_root / "project.py").write_text("def env_selected_root():\n    return True\n", encoding="utf-8")
    (app_root / "ignored.py").write_text("def ignored_root():\n    return True\n", encoding="utf-8")
    monkeypatch.setenv("ASTRA_PROJECT_ROOT", str(project_root))

    with TestClient(create_app(tmp_path / "app.db", workspace_root=app_root)) as client:
        created = client.post("/rag/index")
        files = client.get("/rag/files")

    assert created.status_code == 200
    assert created.json()["root"] == str(project_root.resolve())
    assert [item["path"] for item in files.json()["items"]] == ["project.py"]


def test_project_rag_index_excludes_noisy_and_unsafe_paths(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("def safe_code():\n    return 'indexed'\n", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "bad.js").write_text("const secret = 'noise';", encoding="utf-8")
    (tmp_path / ".env").write_text("TOKEN=do-not-index", encoding="utf-8")
    (tmp_path / "package-lock.json").write_text('{"name": "noise"}', encoding="utf-8")
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "weights.json").write_text('{"noise": true}', encoding="utf-8")

    with TestClient(create_app(tmp_path / "app.db", workspace_root=tmp_path)) as client:
        client.post("/rag/index")
        files = client.get("/rag/files")
        search = client.post("/rag/search", json={"query": "noise token", "limit": 10})

    paths = [item["path"] for item in files.json()["items"]]
    assert paths == ["src/main.py"]
    assert "do-not-index" not in str(search.json())
    assert "node_modules" not in str(search.json())
    assert "package-lock" not in str(search.json())
    assert "models" not in str(search.json())


def test_project_rag_index_indexes_allowed_extensions(tmp_path: Path):
    allowed = {
        "a.py": "python marker",
        "b.ts": "typescript marker",
        "c.tsx": "tsx marker",
        "d.js": "javascript marker",
        "e.jsx": "jsx marker",
        "f.json": '{"json": "marker"}',
        "g.md": "markdown marker",
        "h.txt": "text marker",
        "i.css": ".class { color: red; }",
        "j.html": "<main>html marker</main>",
        "k.toml": 'name = "toml marker"',
        "l.yaml": "yaml: marker",
        "m.yml": "yml: marker",
    }
    for name, text in allowed.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    (tmp_path / "skip.bin").write_bytes(b"marker")

    with TestClient(create_app(tmp_path / "app.db", workspace_root=tmp_path)) as client:
        created = client.post("/rag/index")
        files = client.get("/rag/files")

    paths = {item["path"] for item in files.json()["items"]}
    assert created.json()["indexed_files"] == len(allowed)
    assert paths == set(allowed)
    assert "skip.bin" not in paths


def test_project_rag_search_returns_path_and_line_numbers(tmp_path: Path):
    lines = [f"line {index}" for index in range(1, 121)]
    lines[87] = "phase fifty two project indexing target"
    (tmp_path / "guide.md").write_text("\n".join(lines), encoding="utf-8")

    with TestClient(create_app(tmp_path / "app.db", workspace_root=tmp_path)) as client:
        client.post("/rag/index")
        search = client.post(
            "/rag/search",
            json={"query": "project indexing target", "limit": 3},
        )

    assert search.status_code == 200
    body = search.json()
    assert body["status"] == "ready"
    assert body["results"]
    top = body["results"][0]
    assert top["path"] == "guide.md"
    assert top["start_line"] <= 88 <= top["end_line"]
    assert "project indexing target" in top["snippet"]


def test_rag_evaluation_requires_project_index(tmp_path: Path):
    with TestClient(create_app(tmp_path / "app.db", workspace_root=tmp_path)) as client:
        status = client.get("/rag/evaluation/status")
        evaluation = client.post("/rag/evaluate", json={})

    assert status.status_code == 200
    assert status.json()["index_exists"] is False
    assert status.json()["evaluation_case_count"] >= 3
    assert evaluation.status_code == 200
    body = evaluation.json()
    assert body["status"] == "index_missing"
    assert body["index_exists"] is False
    assert body["total_cases"] == 0
    assert "requires an existing project index" in body["message"]


def test_rag_evaluation_returns_metrics_and_path_hits(tmp_path: Path):
    target = tmp_path / "backend" / "app" / "rag"
    target.mkdir(parents=True)
    (target / "project_indexer.py").write_text(
        "def build_project_index():\n"
        "    return 'project RAG indexing implemented here'\n\n"
        "def search_project_index():\n"
        "    return 'project index search'\n",
        encoding="utf-8",
    )

    with TestClient(create_app(tmp_path / "app.db", workspace_root=tmp_path)) as client:
        client.post("/rag/index")
        evaluation = client.post(
            "/rag/evaluate",
            json={"selected_cases": ["project-rag-indexing"]},
        )

    assert evaluation.status_code == 200
    body = evaluation.json()
    assert body["status"] == "ready"
    assert body["total_cases"] == 1
    assert body["passed_cases"] == 1
    assert body["failed_cases"] == 0
    assert body["path_hit_rate"] == 1.0
    assert body["average_top_score"] > 0
    assert body["average_sources_returned"] >= 1
    case = body["cases"][0]
    assert case["passed"] is True
    assert case["expected_paths"] == ["backend/app/rag/project_indexer.py"]
    assert "backend/app/rag/project_indexer.py" in case["returned_paths"]
    assert case["missing_expected_paths"] == []


def test_rag_evaluation_persists_latest_result(tmp_path: Path):
    target = tmp_path / "backend" / "app" / "rag"
    target.mkdir(parents=True)
    (target / "project_indexer.py").write_text(
        "build_project_index search_project_index project RAG indexing",
        encoding="utf-8",
    )

    with TestClient(create_app(tmp_path / "app.db", workspace_root=tmp_path)) as client:
        client.post("/rag/index")
        evaluation = client.post(
            "/rag/evaluate",
            json={"selected_cases": ["project-rag-indexing"]},
        )
        status = client.get("/rag/evaluation/status")

    persisted_path = tmp_path / "data" / "rag" / "latest_evaluation.json"
    assert persisted_path.exists()
    persisted = json.loads(persisted_path.read_text(encoding="utf-8"))
    assert persisted["created_at"] == evaluation.json()["created_at"]
    assert persisted["passed_cases"] == 1
    assert status.json()["latest_evaluation"]["created_at"] == evaluation.json()["created_at"]


def test_chat_workflow_without_canonical_project_never_uses_the_legacy_project_index(
    tmp_path: Path,
) -> None:
    """Canonical chat retrieval is project-bound only (see Phase 9): even
    with a populated legacy /rag/index and a clearly project-shaped
    question, no canonical project_run_id means no retrieval occurs."""

    database_path = tmp_path / "app.db"
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "phase52.md").write_text(
        "Phase 52 project indexing should provide path and line aware snippets.",
        encoding="utf-8",
    )

    with TestClient(create_app(database_path, workspace_root=tmp_path)) as client:
        client.post("/rag/index")
        response = client.post(
            "/chat/run",
            json={"message": "What should Phase 52 project indexing provide?", "use_rag": True},
        )
        runs = client.get("/chat/runs")

    assert response.status_code == 200
    body = response.json()
    assert body["rag_used"] is False
    assert body["rag_context_count"] == 0
    assert body["rag_skip_reason"] == "no_canonical_project"
    assert body["source_count"] == 0
    items = runs.json()["items"]
    assert len(items) == 1
    assert items[0]["run_id"] == body["run_id"]
