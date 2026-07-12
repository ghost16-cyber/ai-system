from __future__ import annotations

import builtins
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from backend.app.main import create_app


def test_assignments_status_endpoint(tmp_path: Path):
    with TestClient(create_app(tmp_path / "app.db", workspace_root=tmp_path)) as client:
        response = client.get("/assignments/status")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert ".txt" in body["supported_extensions"]
    assert body["tools_executed"] is False



def test_assignment_upload_saves_supported_file_inside_workspace(tmp_path: Path):
    content = b"# Uploaded Portfolio\nTask: Build a safe pipeline.\n"
    with TestClient(create_app(tmp_path / "app.db", workspace_root=tmp_path)) as client:
        uploaded = client.post(
            "/assignments/upload",
            params={"filename": "../My Portfolio.md"},
            content=content,
            headers={"content-type": "application/octet-stream"},
        )
        parsed = client.post(
            "/assignments/parse",
            json={"path": uploaded.json()["path"]},
        )

    assert uploaded.status_code == 200, uploaded.text
    body = uploaded.json()
    assert body["filename"] == "My_Portfolio.md"
    assert body["path"].startswith("data/assignment_uploads/")
    assert body["size_bytes"] == len(content)
    saved = tmp_path / body["path"]
    assert saved.is_file()
    assert saved.read_bytes() == content
    assert parsed.status_code == 200
    assert parsed.json()["title"] == "Uploaded Portfolio"


def test_assignment_upload_rejects_unsupported_and_empty_files(tmp_path: Path):
    with TestClient(create_app(tmp_path / "app.db", workspace_root=tmp_path)) as client:
        unsupported = client.post(
            "/assignments/upload",
            params={"filename": "brief.exe"},
            content=b"not allowed",
        )
        empty = client.post(
            "/assignments/upload",
            params={"filename": "brief.txt"},
            content=b"",
        )

    assert unsupported.status_code == 400
    assert "Unsupported" in unsupported.json()["detail"]
    assert empty.status_code == 400
    assert "empty" in empty.json()["detail"].lower()
    upload_root = tmp_path / "data" / "assignment_uploads"
    assert not upload_root.exists()


def test_assignments_parse_extract_and_plan_endpoints(tmp_path: Path):
    brief = tmp_path / "brief.md"
    brief.write_text(
        "# Big Data Portfolio\n"
        "Assignment 1: Apache Kafka + InfluxDB + Grafana\n"
        "Task: Build a Kafka pipeline and Grafana dashboard. 20 marks\n"
        "Screenshot required: show Kafka output and Grafana dashboard.\n"
        "Analysis question: Explain the streaming design?\n",
        encoding="utf-8",
    )

    with TestClient(create_app(tmp_path / "app.db", workspace_root=tmp_path)) as client:
        parsed = client.post("/assignments/parse", json={"path": "brief.md"})
        extracted = client.post("/assignments/extract", json={"path": "brief.md"})
        planned = client.post("/assignments/plan", json={"path": "brief.md"})

    assert parsed.status_code == 200
    assert parsed.json()["title"] == "Big Data Portfolio"
    assert extracted.status_code == 200
    assert extracted.json()["sections"][0]["tasks"]
    assert planned.status_code == 200
    assert planned.json()["checklist"]
    assert any(item["screenshot_needed"] for item in planned.json()["checklist"])


def test_assignments_extract_and_plan_from_inline_text(tmp_path: Path):
    text = (
        "Assignment 1: PySpark + Snowflake\n"
        "Task: Implement a PySpark ETL pipeline. 20 marks\n"
        "Report requirement: explain data quality.\n"
    )

    with TestClient(create_app(tmp_path / "app.db", workspace_root=tmp_path)) as client:
        extracted = client.post("/assignments/extract", json={"title": "Inline brief", "text": text})
        planned = client.post("/assignments/plan", json={"brief": extracted.json()})

    assert extracted.status_code == 200
    assert extracted.json()["title"] == "Inline brief"
    assert planned.status_code == 200
    assert any(item["report_section_needed"] for item in planned.json()["checklist"])


def test_assignments_parse_rejects_paths_outside_workspace(tmp_path: Path):
    outside = tmp_path.parent / "outside_assignment.txt"
    outside.write_text("Assignment 1: outside", encoding="utf-8")

    with TestClient(create_app(tmp_path / "app.db", workspace_root=tmp_path)) as client:
        response = client.post("/assignments/parse", json={"path": str(outside)})

    assert response.status_code == 400
    assert "workspace root" in response.json()["detail"]


def test_assignments_parse_missing_file_returns_404(tmp_path: Path):
    with TestClient(create_app(tmp_path / "app.db", workspace_root=tmp_path)) as client:
        response = client.post("/assignments/parse", json={"path": "missing.md"})

    assert response.status_code == 404


def test_assignments_docx_missing_dependency_returns_controlled_400(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    brief = tmp_path / "brief.docx"
    brief.write_bytes(b"not opened when import fails")
    original_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "docx":
            raise ImportError("missing docx")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with TestClient(create_app(tmp_path / "app.db", workspace_root=tmp_path)) as client:
        response = client.post("/assignments/parse", json={"path": "brief.docx"})

    assert response.status_code == 400
    assert "python-docx is required" in response.json()["detail"]


def test_assignment_copilot_dataset_folder_returns_clear_error(tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    with TestClient(create_app(tmp_path / "app.db", workspace_root=tmp_path)) as client:
        response = client.post(
            "/assignments/copilot/run",
            json={"text": "Assignment 1: Kafka\nTask: Build a pipeline.", "dataset_path": "data"},
        )

    assert response.status_code == 400
    assert "folder" in response.json()["detail"].lower()


def test_assignment_code_write_endpoint_creates_starter_files(tmp_path: Path):
    workspace = tmp_path / "assignment_workspaces" / "a1"

    with TestClient(create_app(tmp_path / "app.db", workspace_root=tmp_path)) as client:
        response = client.post(
            "/assignments/code/write",
            json={"assignment_number": 1, "workspace_path": "assignment_workspaces/a1"},
        )

    assert response.status_code == 200
    body = response.json()
    assert "producer.py" in body["created_files"]
    assert body["commands_executed"] is False
    assert (workspace / "producer.py").exists()


def test_assignment_dataset_map_endpoint_returns_suggestions(tmp_path: Path):
    dataset = tmp_path / "events.csv"
    dataset.write_text("event_time,value,site\n2026-01-01,10,A\n", encoding="utf-8")

    with TestClient(create_app(tmp_path / "app.db", workspace_root=tmp_path)) as client:
        response = client.post("/assignments/dataset/map", json={"dataset_path": "events.csv"})

    assert response.status_code == 200
    assert response.json()["timestamp_column"]["column"] == "event_time"


def test_assignment_manifest_write_endpoint_creates_manifest(tmp_path: Path):
    copilot_result = {
        "parsed_document_summary": {"source_path": "brief.md"},
        "code_blueprints": [{"blueprints": [{"file_path": "producer.py"}]}],
        "evidence_checklist": {"title": "Evidence", "summary": {}, "items": []},
        "runbooks": [{"steps": [{"step_id": "1", "title": "Review"}]}],
        "safe_next_commands": [],
        "final_readiness": {"readiness_level": "not_started", "missing_screenshots": [], "missing_report_sections": []},
    }

    with TestClient(create_app(tmp_path / "app.db", workspace_root=tmp_path)) as client:
        response = client.post(
            "/assignments/manifest/write",
            json={
                "assignment_number": 1,
                "workspace_path": "assignment_workspaces/a1",
                "copilot_result": copilot_result,
            },
        )

    assert response.status_code == 200
    assert response.json()["written"] is True
    assert (tmp_path / "assignment_workspaces" / "a1" / "assignment_manifest.json").exists()


def test_assignment_manifest_write_endpoint_refuses_outside_workspace(tmp_path: Path):
    outside = tmp_path.parent / "outside_workspace"

    with TestClient(create_app(tmp_path / "app.db", workspace_root=tmp_path)) as client:
        response = client.post(
            "/assignments/manifest/write",
            json={
                "assignment_number": 1,
                "workspace_path": str(outside),
                "copilot_result": {"final_readiness": {}},
            },
        )

    assert response.status_code == 400
    assert "outside the allowed workspace root" in response.json()["detail"]
