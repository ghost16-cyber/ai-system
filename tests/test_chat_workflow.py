from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.chat_actions import detect_chat_action
from backend.app.main import create_app


def _ndjson_events(response) -> list[dict]:
    return [
        json.loads(line)
        for line in response.text.splitlines()
        if line.strip()
    ]


def _chat_runs(client: TestClient) -> list[dict]:
    response = client.get("/chat/runs")
    assert response.status_code == 200, response.text
    return response.json()["items"]


def _stored_run(client: TestClient, run_id: str) -> dict:
    matches = [item for item in _chat_runs(client) if item["run_id"] == run_id]
    assert len(matches) == 1
    return matches[0]


def _command_plan(run: dict) -> dict:
    return run["action"]["technical_details"]["command_plan"]


def _command_association(run: dict) -> dict:
    plan = _command_plan(run)
    return {
        "assignment_id": plan["assignment_id"],
        "workspace_path": plan["workspace"],
        "chat_run_id": run["run_id"],
    }


ASSIGNMENT_BRIEF = """
Assignment 2: PySpark + Snowflake + Streamlit
Task: Clean data with PySpark, load it into Snowflake, and build a Streamlit dashboard. 25 marks
Screenshot required: Snowflake worksheet and Streamlit dashboard.
Analysis question: Explain the data pipeline and dashboard design.
"""


def _assignment_action(run: dict) -> dict:
    action = run["action"]
    assert action["action_type"] == "assignment"
    return action


def _workspace_action(run: dict) -> dict:
    return _assignment_action(run)["technical_details"]["workspace_action"]


def _folder_action(run: dict) -> dict:
    action = run["action"]
    assert action["action_type"] == "folder_access"
    return action


def _folder_details(run: dict) -> dict:
    return _folder_action(run)["technical_details"]["folder_action"]


def _request_folder(client: TestClient, folder: Path, message: str | None = None) -> dict:
    response = client.post(
        "/chat/run",
        json={"message": message or f"Use {folder}", "use_rag": True},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _approve_folder(client: TestClient, run: dict):
    action_id = _folder_action(run)["action_id"]
    return client.post(
        f"/chat/folders/{action_id}/approve",
        json={"chat_run_id": run["run_id"]},
    )


def _cancel_folder(client: TestClient, run: dict):
    action_id = _folder_action(run)["action_id"]
    return client.post(
        f"/chat/folders/{action_id}/cancel",
        json={"chat_run_id": run["run_id"]},
    )


def _rescan_folder(client: TestClient, run: dict):
    action_id = _folder_action(run)["action_id"]
    return client.post(
        f"/chat/folders/{action_id}/rescan",
        json={"chat_run_id": run["run_id"]},
    )


def _make_folder_project(tmp_path: Path) -> Path:
    project = tmp_path / "folder_project"
    (project / "src").mkdir(parents=True)
    (project / "data").mkdir()
    (project / ".git").mkdir()
    (project / ".venv").mkdir()
    (project / "node_modules").mkdir()
    (project / "README.md").write_text("Astra folder project\n", encoding="utf-8")
    (project / "assignment_brief.md").write_text("Do the assignment. SUPERSECRET-CONTENT\n", encoding="utf-8")
    (project / "src" / "app.py").write_text("print('should not execute during scan')\n", encoding="utf-8")
    (project / "data" / "events.csv").write_text("day,value\n1,2\n", encoding="utf-8")
    (project / "evidence.png").write_bytes(b"\x89PNG\r\n")
    (project / ".env").write_text("TOKEN=do-not-store\n", encoding="utf-8")
    (project / ".git" / "config").write_text("[core]\n", encoding="utf-8")
    (project / ".venv" / "pyvenv.cfg").write_text("home=/tmp\n", encoding="utf-8")
    (project / "node_modules" / "package.json").write_text("{}", encoding="utf-8")
    (project / "local.sqlite").write_bytes(b"sqlite")
    (project / "model.pt").write_bytes(b"weights")
    outside = tmp_path / "outside_secret.txt"
    outside.write_text("outside", encoding="utf-8")
    try:
        (project / "outside-link").symlink_to(outside)
    except OSError:
        pass
    return project


def test_chat_assignment_analysis_and_workspace_action_are_persisted(
    tmp_path: Path,
) -> None:
    with TestClient(create_app(tmp_path / "app.db", workspace_root=tmp_path)) as client:
        response = client.post(
            "/chat/assignments/analyze",
            json={
                "text": ASSIGNMENT_BRIEF,
                "selected_assignment": 2,
                "user_message": "Read this assignment",
            },
        )
        runs = _chat_runs(client)
        detail = client.get(
            f"/chat/conversations/{response.json()['conversation_id']}",
        )

    assert response.status_code == 200, response.text
    assert len(runs) == 1
    run = runs[0]
    action = _assignment_action(run)
    analysis = action["technical_details"]["assignment_analysis"]
    workspace = action["technical_details"]["workspace_action"]
    copilot_result = action["technical_details"]["copilot_result"]
    assert run["run_id"] == response.json()["run_id"]
    assert action["status"] == "awaiting_approval"
    assert analysis["title"] == "Assignment 2: PySpark + Snowflake + Streamlit"
    assert analysis["section_count"] >= 1
    assert analysis["task_count"] >= 1
    assert analysis["evidence_count"] >= 1
    assert analysis["report_section_count"] >= 1
    assert analysis["next_recommended_step"]
    assert workspace["status"] == "awaiting_approval"
    assert workspace["planned_file_count"] > 0
    assert workspace["targets"][0]["assignment_number"] == 2
    assert "extracted_text" not in copilot_result["parsed_document_summary"]
    assert detail.status_code == 200
    assert (
        detail.json()["turns"][0]["action"]["technical_details"]["assignment_analysis"]
        == analysis
    )


def test_chat_assignment_workspace_approval_updates_same_persisted_record(
    tmp_path: Path,
) -> None:
    with TestClient(create_app(tmp_path / "app.db", workspace_root=tmp_path)) as client:
        created = client.post(
            "/chat/assignments/analyze",
            json={
                "text": ASSIGNMENT_BRIEF,
                "selected_assignment": 2,
                "user_message": "Read this assignment",
            },
        ).json()
        run = _stored_run(client, created["run_id"])
        action = _assignment_action(run)
        action_id = action["action_id"]

        approved = client.post(
            f"/chat/assignments/workspace/{action_id}/approve",
            json={"chat_run_id": run["run_id"]},
        )
        updated = _stored_run(client, run["run_id"])
        runs = _chat_runs(client)
        detail = client.get(f"/chat/conversations/{run['conversation_id']}")

    assert approved.status_code == 200, approved.text
    assert approved.json()["run_id"] == run["run_id"]
    assert len(runs) == 1
    updated_action = _assignment_action(updated)
    workspace = _workspace_action(updated)
    assert updated_action["status"] == "completed"
    assert workspace["status"] == "completed"
    assert updated_action["result_summary"].startswith("Created ")
    assert workspace["result_summary"] == updated_action["result_summary"]
    assert workspace["results"][0]["workspace_path"] == "assignment_workspaces/assignment_2"
    assert workspace["results"][0]["created_files"]
    assert workspace["results"][0]["commands_executed"] is False
    assert workspace["results"][0]["generated_code_executed"] is False
    assert (tmp_path / "assignment_workspaces" / "assignment_2").is_dir()
    restored_action = detail.json()["turns"][0]["action"]
    assert restored_action["status"] == "completed"
    assert (
        restored_action["technical_details"]["workspace_action"]["results"][0]["created_files"]
        == workspace["results"][0]["created_files"]
    )


def test_chat_assignment_workspace_cancellation_updates_same_record_without_files(
    tmp_path: Path,
) -> None:
    with TestClient(create_app(tmp_path / "app.db", workspace_root=tmp_path)) as client:
        created = client.post(
            "/chat/assignments/analyze",
            json={
                "text": ASSIGNMENT_BRIEF,
                "selected_assignment": 2,
                "user_message": "Read this assignment",
            },
        ).json()
        run = _stored_run(client, created["run_id"])
        action_id = _assignment_action(run)["action_id"]

        cancelled = client.post(
            f"/chat/assignments/workspace/{action_id}/cancel",
            json={"chat_run_id": run["run_id"]},
        )
        updated = _stored_run(client, run["run_id"])
        runs = _chat_runs(client)

    assert cancelled.status_code == 200, cancelled.text
    assert len(runs) == 1
    assert _assignment_action(updated)["status"] == "cancelled"
    workspace = _workspace_action(updated)
    assert workspace["status"] == "cancelled"
    assert workspace["result_summary"] == "Workspace creation cancelled. No files were written."
    assert not (tmp_path / "assignment_workspaces").exists()


def test_chat_assignment_workspace_rejects_mismatched_run_without_modifying_records(
    tmp_path: Path,
) -> None:
    with TestClient(create_app(tmp_path / "app.db", workspace_root=tmp_path)) as client:
        first = client.post(
            "/chat/assignments/analyze",
            json={
                "text": ASSIGNMENT_BRIEF,
                "selected_assignment": 2,
                "user_message": "Read first assignment",
            },
        ).json()
        second = client.post(
            "/chat/assignments/analyze",
            json={
                "text": ASSIGNMENT_BRIEF,
                "selected_assignment": 2,
                "user_message": "Read second assignment",
            },
        ).json()
        first_before = _stored_run(client, first["run_id"])
        second_before = _stored_run(client, second["run_id"])
        action_id = _assignment_action(first_before)["action_id"]

        rejected = client.post(
            f"/chat/assignments/workspace/{action_id}/approve",
            json={"chat_run_id": second["run_id"]},
        )
        first_after = _stored_run(client, first["run_id"])
        second_after = _stored_run(client, second["run_id"])
        runs = _chat_runs(client)

    assert rejected.status_code == 409
    assert len(runs) == 2
    assert first_after["action"] == first_before["action"]
    assert second_after["action"] == second_before["action"]
    assert not (tmp_path / "assignment_workspaces").exists()


def test_folder_request_creates_awaiting_action_without_scanning(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from backend.app import main as main_module

    project = _make_folder_project(tmp_path)

    def ordinary_workflow_must_not_run(*args, **kwargs):
        raise AssertionError("ordinary workflow was invoked")

    monkeypatch.setattr(main_module, "run_chat_workflow", ordinary_workflow_must_not_run)
    with TestClient(create_app(tmp_path / "app.db", workspace_root=tmp_path)) as client:
        response = client.post(
            "/chat/run",
            json={"message": f"Use {project}", "use_rag": True},
        )
        runs = _chat_runs(client)

    assert response.status_code == 200, response.text
    assert len(runs) == 1
    action = _folder_action(runs[0])
    details = _folder_details(runs[0])
    assert action["status"] == "awaiting_approval"
    assert action["approval_required"] is True
    assert details["status"] == "awaiting_approval"
    assert details["inventory"] == []
    assert details["scan_count"] == 0


def test_folder_approval_scans_and_persists_sanitized_inventory(
    tmp_path: Path,
) -> None:
    project = _make_folder_project(tmp_path)
    with TestClient(create_app(tmp_path / "app.db", workspace_root=tmp_path)) as client:
        created = _request_folder(client, project, f"Open this project folder: {project}")
        run = _stored_run(client, created["run_id"])
        approval = _approve_folder(client, run)
        updated = _stored_run(client, run["run_id"])
        detail = client.get(f"/chat/conversations/{run['conversation_id']}")
        runs = _chat_runs(client)

    assert approval.status_code == 200, approval.text
    assert approval.json()["run_id"] == run["run_id"]
    assert len(runs) == 1
    action = _folder_action(updated)
    details = _folder_details(updated)
    inventory = details["inventory"]
    paths = {item["relative_path"] for item in inventory}
    assert action["status"] == "completed"
    assert details["status"] == "completed"
    assert details["summary"]["readable"] >= 5
    assert "README.md" in paths
    assert "src/app.py" in paths
    assert "data/events.csv" in paths
    assert all(not Path(path).is_absolute() for path in paths)
    assert all(str(project) not in path for path in paths)
    assert "SUPERSECRET-CONTENT" not in json.dumps(action)
    assert "TOKEN=do-not-store" not in json.dumps(action)
    ignored = {item["relative_path"]: item["ignore_reason"] for item in inventory if item["status"] == "ignored"}
    assert ignored[".env"] == "sensitive_file"
    assert ignored[".git"] == "ignored_directory"
    assert ignored[".venv"] == "ignored_directory"
    assert ignored["node_modules"] == "ignored_directory"
    assert ignored["local.sqlite"] == "blocked_file_type"
    assert ignored["model.pt"] == "blocked_file_type"
    if "outside-link" in ignored:
        assert ignored["outside-link"] == "symlink_escape"
    restored_action = detail.json()["turns"][0]["action"]
    assert restored_action["technical_details"]["folder_action"]["inventory"] == inventory


@pytest.mark.parametrize(
    "message",
    [
        "read those files",
        "read the files",
        "open those files",
        "inspect the file contents",
        "summarize the folder files",
        "analyze those files",
        "read the assignment",
        "read the dataset",
    ],
)
def test_connected_folder_content_request_is_deterministic_without_specialist_or_action(
    tmp_path: Path,
    monkeypatch,
    message: str,
) -> None:
    from backend.app import main as main_module

    project = _make_folder_project(tmp_path)

    def ordinary_workflow_must_not_run(*args, **kwargs):
        raise AssertionError("ordinary specialist, RAG, and SLM workflow was invoked")

    monkeypatch.setattr(main_module, "run_chat_workflow", ordinary_workflow_must_not_run)
    with TestClient(create_app(tmp_path / "app.db", workspace_root=tmp_path)) as client:
        created = _request_folder(client, project)
        folder_run = _stored_run(client, created["run_id"])
        approval = _approve_folder(client, folder_run)
        assert approval.status_code == 200, approval.text
        connected_action = _stored_run(client, folder_run["run_id"])["action"]

        response = client.post(
            "/chat/run",
            json={
                "message": message,
                "conversation_id": folder_run["conversation_id"],
                "use_rag": True,
            },
        )
        runs = _chat_runs(client)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["assistant_response"] == (
        "That folder is connected in metadata-only mode. I can see its inventory, "
        "but file-content reading is not enabled yet."
    )
    assert body["selected_specialist"] == "folder_access"
    assert body["intent"] == "folder_content_unavailable"
    assert body["used_real_slm"] is False
    assert body["slm_provider"] == "not_invoked"
    assert body["rag_used"] is False
    assert body["action"] is None
    assert len(runs) == 2
    original = next(run for run in runs if run["run_id"] == folder_run["run_id"])
    assert original["action"] == connected_action


def test_streamed_connected_folder_content_request_bypasses_specialist(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from backend.app import main as main_module

    project = _make_folder_project(tmp_path)

    def ordinary_workflow_must_not_run(*args, **kwargs):
        raise AssertionError("ordinary specialist, RAG, and SLM workflow was invoked")

    monkeypatch.setattr(main_module, "run_chat_workflow", ordinary_workflow_must_not_run)
    with TestClient(create_app(tmp_path / "app.db", workspace_root=tmp_path)) as client:
        created = _request_folder(client, project)
        folder_run = _stored_run(client, created["run_id"])
        approval = _approve_folder(client, folder_run)
        assert approval.status_code == 200, approval.text

        response = client.post(
            "/chat/stream",
            json={
                "message": "read those files",
                "conversation_id": folder_run["conversation_id"],
                "use_rag": True,
            },
        )
        events = _ndjson_events(response)
        runs = _chat_runs(client)

    assert response.status_code == 200, response.text
    assert [event["event"] for event in events] == ["run_completed"]
    run = events[0]["data"]["run"]
    assert run["intent"] == "folder_content_unavailable"
    assert run["used_real_slm"] is False
    assert run["action"] is None
    assert len(runs) == 2


def test_folder_cancellation_updates_same_record_without_scan(
    tmp_path: Path,
) -> None:
    project = _make_folder_project(tmp_path)
    with TestClient(create_app(tmp_path / "app.db", workspace_root=tmp_path)) as client:
        created = _request_folder(client, project)
        run = _stored_run(client, created["run_id"])
        cancellation = _cancel_folder(client, run)
        updated = _stored_run(client, run["run_id"])
        runs = _chat_runs(client)

    assert cancellation.status_code == 200, cancellation.text
    assert len(runs) == 1
    action = _folder_action(updated)
    details = _folder_details(updated)
    assert action["status"] == "cancelled"
    assert action["result_summary"] == "Folder access cancelled. No folder was scanned."
    assert details["status"] == "cancelled"
    assert details["inventory"] == []
    assert details["scan_count"] == 0


def test_folder_action_rejects_mismatched_chat_run_without_modifying_records(
    tmp_path: Path,
) -> None:
    first_project = _make_folder_project(tmp_path)
    second_project = tmp_path / "second"
    second_project.mkdir()
    with TestClient(create_app(tmp_path / "app.db", workspace_root=tmp_path)) as client:
        first = _request_folder(client, first_project)
        second = _request_folder(client, second_project, f"Scan my assignment folder at {second_project}")
        first_before = _stored_run(client, first["run_id"])
        second_before = _stored_run(client, second["run_id"])
        action_id = _folder_action(first_before)["action_id"]
        rejected = client.post(
            f"/chat/folders/{action_id}/approve",
            json={"chat_run_id": second["run_id"]},
        )
        first_after = _stored_run(client, first["run_id"])
        second_after = _stored_run(client, second["run_id"])
        runs = _chat_runs(client)

    assert rejected.status_code == 409
    assert len(runs) == 2
    assert first_after["action"] == first_before["action"]
    assert second_after["action"] == second_before["action"]


def test_folder_state_conflicts_return_409(
    tmp_path: Path,
) -> None:
    project = _make_folder_project(tmp_path)
    other = tmp_path / "cancelled"
    other.mkdir()
    with TestClient(create_app(tmp_path / "app.db", workspace_root=tmp_path)) as client:
        created = _request_folder(client, project)
        run = _stored_run(client, created["run_id"])
        first_approval = _approve_folder(client, run)
        approved_run = _stored_run(client, run["run_id"])
        second_approval = _approve_folder(client, approved_run)
        cancel_after_completion = _cancel_folder(client, approved_run)

        cancelled = _request_folder(client, other, f"Connect this folder in read-only mode: {other}")
        cancelled_run = _stored_run(client, cancelled["run_id"])
        cancellation = _cancel_folder(client, cancelled_run)
        approval_after_cancel = _approve_folder(client, _stored_run(client, cancelled["run_id"]))

    assert first_approval.status_code == 200, first_approval.text
    assert second_approval.status_code == 409
    assert cancel_after_completion.status_code == 409
    assert cancellation.status_code == 200, cancellation.text
    assert approval_after_cancel.status_code == 409


def test_folder_rescan_reports_added_changed_deleted_and_unchanged_files(
    tmp_path: Path,
) -> None:
    project = tmp_path / "rescan"
    project.mkdir()
    keep = project / "keep.py"
    changed = project / "changed.py"
    deleted = project / "deleted.py"
    keep.write_text("keep = 1\n", encoding="utf-8")
    changed.write_text("value = 1\n", encoding="utf-8")
    deleted.write_text("gone = 1\n", encoding="utf-8")
    with TestClient(create_app(tmp_path / "app.db", workspace_root=tmp_path)) as client:
        created = _request_folder(client, project)
        run = _stored_run(client, created["run_id"])
        approval = _approve_folder(client, run)
        assert approval.status_code == 200, approval.text
        changed.write_text("value = 2\n", encoding="utf-8")
        deleted.unlink()
        (project / "added.py").write_text("added = 1\n", encoding="utf-8")
        rescan = _rescan_folder(client, _stored_run(client, run["run_id"]))
        updated = _stored_run(client, run["run_id"])
        runs = _chat_runs(client)

    assert rescan.status_code == 200, rescan.text
    assert len(runs) == 1
    diff = _folder_details(updated)["diff"]
    assert diff["added"] == 1
    assert diff["changed"] == 1
    assert diff["deleted"] == 1
    assert diff["unchanged"] == 1
    assert _folder_details(updated)["scan_count"] == 2


@pytest.mark.parametrize(
    ("path_factory", "status_code"),
    [
        (lambda base: base / "missing-folder", 404),
        (lambda base: base / "regular-file.txt", 400),
        (lambda base: base / "project" / ".." / "project", 400),
    ],
)
def test_folder_invalid_paths_fail_controlled(
    tmp_path: Path,
    path_factory,
    status_code: int,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (tmp_path / "regular-file.txt").write_text("not a folder", encoding="utf-8")
    requested = path_factory(tmp_path)
    with TestClient(create_app(tmp_path / "app.db", workspace_root=tmp_path)) as client:
        created = client.post(
            "/chat/folders/request",
            json={"path": str(requested), "user_message": f"Use {requested}"},
        )
        assert created.status_code == 200, created.text
        run = _stored_run(client, created.json()["run_id"])
        approval = _approve_folder(client, run)
        updated = _stored_run(client, run["run_id"])

    assert approval.status_code == status_code
    assert _folder_action(updated)["status"] == "failed"
    assert _folder_details(updated)["error"]


def test_folder_scan_limits_warn_and_generated_files_are_not_executed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from backend.app.folders import scanner

    project = tmp_path / "limits"
    project.mkdir()
    marker = tmp_path / "executed.txt"
    (project / "would_execute.py").write_text(
        "from pathlib import Path\nPath(r'%s').write_text('ran', encoding='utf-8')\n" % marker,
        encoding="utf-8",
    )
    for index in range(5):
        (project / f"file_{index}.py").write_text(f"value = {index}\n", encoding="utf-8")
    monkeypatch.setattr(scanner, "MAX_FILES", 3)
    with TestClient(create_app(tmp_path / "app.db", workspace_root=tmp_path)) as client:
        created = _request_folder(client, project)
        run = _stored_run(client, created["run_id"])
        approval = _approve_folder(client, run)
        updated = _stored_run(client, run["run_id"])

    assert approval.status_code == 200, approval.text
    details = _folder_details(updated)
    assert details["warnings"]
    assert not marker.exists()


@pytest.mark.parametrize("phrase", ["run the test", "run the tests", "run pytest"])
def test_direct_test_command_is_intercepted_before_chat_workflow(
    tmp_path: Path,
    monkeypatch,
    phrase: str,
) -> None:
    from backend.app import main as main_module

    def ordinary_workflow_must_not_run(*args, **kwargs):
        raise AssertionError("ordinary specialist, RAG, and SLM workflow was invoked")

    monkeypatch.setattr(main_module, "run_chat_workflow", ordinary_workflow_must_not_run)
    with TestClient(create_app(tmp_path / "app.db", workspace_root=tmp_path)) as client:
        response = client.post("/chat/run", json={"message": phrase, "use_rag": True})
        history = client.get("/chat/runs").json()["items"]

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["action"]["status"] == "awaiting_approval"
    assert body["action"]["technical_details"]["command_plan"]["command"] == "python -m pytest -q"
    assert body["selected_specialist"] == "command_action"
    assert body["rag_used"] is False
    assert body["used_real_slm"] is False
    assert body["action"]["technical_details"]["command_plan"]["exit_code"] is None
    assert len(list((tmp_path / "data" / "assignment_command_runs").glob("*.json"))) == 1
    assert history[0]["action"]["status"] == "awaiting_approval"
    assert not (tmp_path / "data" / "training" / "intent_examples.jsonl").exists()


def test_direct_action_stream_has_one_response_and_no_generated_answer(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path / "app.db", workspace_root=tmp_path)) as client:
        response = client.post("/chat/stream", json={"message": "pytest", "use_rag": True})

    events = _ndjson_events(response)
    assert [event["event"] for event in events] == ["run_completed"]
    assert events[0]["data"]["run"]["action"]["status"] == "awaiting_approval"


def test_direct_chat_command_creates_one_persisted_awaiting_action(
    tmp_path: Path,
) -> None:
    with TestClient(create_app(tmp_path / "app.db", workspace_root=tmp_path)) as client:
        response = client.post(
            "/chat/run",
            json={"message": "run the tests", "use_rag": True},
        )
        runs = _chat_runs(client)

    assert response.status_code == 200, response.text
    assert len(runs) == 1
    run = runs[0]
    assert run["run_id"] == response.json()["run_id"]
    assert run["action"]["status"] == "awaiting_approval"
    assert _command_plan(run)["command"] == "python -m pytest -q"


def test_chat_command_approval_updates_same_persisted_run(
    tmp_path: Path,
) -> None:
    with TestClient(create_app(tmp_path / "app.db", workspace_root=tmp_path)) as client:
        created = client.post(
            "/chat/run",
            json={"message": "run the tests", "use_rag": True},
        ).json()
        run = _stored_run(client, created["run_id"])
        plan = _command_plan(run)

        approval = client.post(
            f"/assignments/commands/{plan['plan_id']}/approve",
            json={
                **_command_association(run),
                "confirmation": f"APPROVE {plan['plan_id']}",
            },
        )
        updated = _stored_run(client, created["run_id"])
        runs = _chat_runs(client)

    assert approval.status_code == 200, approval.text
    assert len(runs) == 1
    assert updated["action"]["status"] == "approved"
    assert _command_plan(updated)["plan_id"] == plan["plan_id"]
    assert _command_plan(updated)["status"] == "approved"


def test_chat_command_execution_updates_same_run_with_result_summary(
    tmp_path: Path,
) -> None:
    (tmp_path / "test_chat_command_smoke.py").write_text(
        "def test_fast_chat_command_target():\n"
        "    assert 2 + 2 == 4\n",
        encoding="utf-8",
    )

    with TestClient(create_app(tmp_path / "app.db", workspace_root=tmp_path)) as client:
        created = client.post(
            "/chat/run",
            json={"message": "run the tests", "use_rag": True},
        ).json()
        run = _stored_run(client, created["run_id"])
        plan = _command_plan(run)
        association = _command_association(run)

        approval = client.post(
            f"/assignments/commands/{plan['plan_id']}/approve",
            json={
                **association,
                "confirmation": f"APPROVE {plan['plan_id']}",
            },
        )
        assert approval.status_code == 200, approval.text

        execution = client.post(
            f"/assignments/commands/{plan['plan_id']}/execute",
            json={
                **association,
                "approval_token": approval.json()["approval_token"],
            },
        )
        updated = _stored_run(client, created["run_id"])
        runs = _chat_runs(client)

    assert execution.status_code == 200, execution.text
    result = execution.json()
    assert result["exit_code"] == 0
    assert len(runs) == 1
    assert updated["action"]["status"] == "completed"
    assert re.fullmatch(
        r"\d+ tests? passed in [0-9.]+ seconds\.",
        updated["action"]["result_summary"],
    )
    persisted_plan = _command_plan(updated)
    assert persisted_plan["plan_id"] == plan["plan_id"]
    assert persisted_plan["exit_code"] == 0
    assert persisted_plan["display_state"] == "completed"
    assert "1 passed" in persisted_plan["stdout"]


def test_chat_command_cancel_updates_same_run_without_execution(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "cancel_marker.txt"
    (tmp_path / "test_cancel_should_not_run.py").write_text(
        "from pathlib import Path\n\n"
        "def test_would_write_marker():\n"
        "    Path('cancel_marker.txt').write_text('ran', encoding='utf-8')\n",
        encoding="utf-8",
    )

    with TestClient(create_app(tmp_path / "app.db", workspace_root=tmp_path)) as client:
        created = client.post(
            "/chat/run",
            json={"message": "run the tests", "use_rag": True},
        ).json()
        run = _stored_run(client, created["run_id"])
        plan = _command_plan(run)

        cancellation = client.post(
            f"/assignments/commands/{plan['plan_id']}/cancel",
            json=_command_association(run),
        )
        updated = _stored_run(client, created["run_id"])
        runs = _chat_runs(client)

    assert cancellation.status_code == 200, cancellation.text
    assert len(runs) == 1
    assert updated["action"]["status"] == "cancelled"
    assert (
        updated["action"]["result_summary"]
        == "Action cancelled. No command was executed."
    )
    assert _command_plan(updated)["exit_code"] is None
    assert not marker.exists()


def test_chat_command_rejects_mismatched_chat_run_id_without_modifying_runs(
    tmp_path: Path,
) -> None:
    with TestClient(create_app(tmp_path / "app.db", workspace_root=tmp_path)) as client:
        first = client.post(
            "/chat/run",
            json={"message": "run the tests", "use_rag": True},
        ).json()
        second = client.post(
            "/chat/run",
            json={"message": "run pytest", "use_rag": True},
        ).json()
        first_run_before = _stored_run(client, first["run_id"])
        second_run_before = _stored_run(client, second["run_id"])
        first_plan = _command_plan(first_run_before)

        rejected = client.post(
            f"/assignments/commands/{first_plan['plan_id']}/approve",
            json={
                **_command_association(first_run_before),
                "chat_run_id": second["run_id"],
                "confirmation": f"APPROVE {first_plan['plan_id']}",
            },
        )

        first_run_after = _stored_run(client, first["run_id"])
        second_run_after = _stored_run(client, second["run_id"])
        runs = _chat_runs(client)

    assert rejected.status_code == 409
    assert len(runs) == 2
    assert first_run_after["action"] == first_run_before["action"]
    assert second_run_after["action"] == second_run_before["action"]


@pytest.mark.parametrize(
    "question",
    ["what does pytest do?", "explain how testing works", "show me a pytest example", "why are tests failing?"],
)
def test_informational_test_questions_remain_ordinary_chat(question: str) -> None:
    assert detect_chat_action(question) is None


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


def test_chat_stream_emits_ordered_workflow_events_and_final_summary(tmp_path: Path):
    with TestClient(create_app(tmp_path / "app.db", workspace_root=tmp_path)) as client:
        response = client.post(
            "/chat/stream",
            json={
                "message": "Safely check this repo without applying patches.",
                "use_rag": False,
            },
        )

    assert response.status_code == 200
    events = _ndjson_events(response)
    event_names = [event["event"] for event in events]
    assert event_names[:4] == [
        "run_started",
        "specialist_selected",
        "rag_completed",
        "safety_completed",
    ]
    assert "response_delta" in event_names
    assert event_names[-1] == "run_completed"

    completed = events[-1]["data"]["run"]
    response_text = "".join(
        event["data"]["delta"]
        for event in events
        if event["event"] == "response_delta"
    )
    assert completed["run_id"]
    assert completed["conversation_id"]
    assert completed["assistant_response"] == response_text
    assert completed["rag_used"] is False
    assert completed["rag_skip_reason"] == "disabled"
    assert completed["trace_summary"]


def test_chat_stream_saves_one_history_run(tmp_path: Path):
    database_path = tmp_path / "app.db"
    with TestClient(create_app(database_path, workspace_root=tmp_path)) as client:
        response = client.post(
            "/chat/stream",
            json={"message": "Explain runtime safety", "use_rag": False},
        )
        runs = client.get("/chat/runs")

    assert response.status_code == 200
    events = _ndjson_events(response)
    completed = events[-1]["data"]["run"]
    assert runs.status_code == 200
    items = runs.json()["items"]
    assert len(items) == 1
    assert items[0]["run_id"] == completed["run_id"]

    with sqlite3.connect(database_path) as connection:
        stored_count = connection.execute("SELECT COUNT(*) FROM chat_runs").fetchone()[0]
    assert stored_count == 1


def test_chat_stream_failure_reports_run_failed_and_chat_run_fallback_still_works(
    tmp_path: Path,
    monkeypatch,
):
    from backend.app import main as main_module

    original_workflow = main_module.run_chat_workflow

    def stream_only_failure(*args, **kwargs):
        if kwargs.get("event_sink") is not None:
            raise RuntimeError("stream failed")
        return original_workflow(*args, **kwargs)

    monkeypatch.setattr(main_module, "run_chat_workflow", stream_only_failure)

    with TestClient(create_app(tmp_path / "app.db", workspace_root=tmp_path)) as client:
        stream_response = client.post(
            "/chat/stream",
            json={"message": "Fallback please", "use_rag": False},
        )
        fallback_response = client.post(
            "/chat/run",
            json={"message": "Fallback please", "use_rag": False},
        )
        runs = client.get("/chat/runs")

    assert stream_response.status_code == 200
    events = _ndjson_events(stream_response)
    assert events[-1]["event"] == "run_failed"
    assert "stream failed" in events[-1]["data"]["error"]
    assert fallback_response.status_code == 200
    assert fallback_response.json()["assistant_response"]
    assert len(runs.json()["items"]) == 1


def test_chat_stream_continues_existing_conversation_with_memory(tmp_path: Path):
    first_message = "Safely inspect the backend chat workflow."
    with TestClient(create_app(tmp_path / "app.db", workspace_root=tmp_path)) as client:
        first = client.post(
            "/chat/run",
            json={"message": first_message, "use_rag": False},
        )
        response = client.post(
            "/chat/stream",
            json={
                "message": "What did I ask first?",
                "use_rag": False,
                "conversation_id": first.json()["conversation_id"],
            },
        )

    assert response.status_code == 200
    events = _ndjson_events(response)
    completed = events[-1]["data"]["run"]
    assert completed["memory_used"] is True
    assert first_message in completed["memory_summary"]
    assert first_message in completed["assistant_response"]


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
