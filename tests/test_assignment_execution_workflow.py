from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.main import create_app


ASSIGNMENT_ID = "assignment-2"
WORKSPACE_PATH = "assignment_workspaces/assignment_2"


def _setup(tmp_path: Path) -> tuple[TestClient, Path]:
    workspace = tmp_path / WORKSPACE_PATH
    workspace.mkdir(parents=True)
    return TestClient(create_app(tmp_path / "app.db", workspace_root=tmp_path)), workspace


def _plan(client: TestClient, *, target: str, assignment_id: str = ASSIGNMENT_ID) -> dict:
    response = client.post(
        "/assignments/commands/plan",
        json={
            "assignment_id": assignment_id,
            "workspace_path": WORKSPACE_PATH,
            "assignment_task": "Run the generated validation entry point",
            "expected_result": "A validation result and redacted logs",
            "action": "python_script",
            "target": target,
            "timeout_seconds": 10,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _association(**values) -> dict:
    return {"assignment_id": ASSIGNMENT_ID, "workspace_path": WORKSPACE_PATH, **values}


def _approve(client: TestClient, plan_id: str) -> str:
    response = client.post(
        f"/assignments/commands/{plan_id}/approve",
        json=_association(confirmation=f"APPROVE {plan_id}"),
    )
    assert response.status_code == 200, response.text
    return response.json()["approval_token"]


def test_plan_is_bound_to_assignment_workspace_task_and_expected_result(tmp_path: Path) -> None:
    client, workspace = _setup(tmp_path)
    (workspace / "main.py").write_text("print('ok')\n", encoding="utf-8")
    other = tmp_path / "assignment_workspaces" / "assignment_3"
    other.mkdir()
    with client:
        plan = _plan(client, target="main.py")
        wrong_assignment = client.post(
            f"/assignments/commands/{plan['plan_id']}/approve",
            json={**_association(confirmation=f"APPROVE {plan['plan_id']}"), "assignment_id": "assignment-3"},
        )
        wrong_workspace = client.post(
            f"/assignments/commands/{plan['plan_id']}/approve",
            json={**_association(confirmation=f"APPROVE {plan['plan_id']}"), "workspace_path": "assignment_workspaces/assignment_3"},
        )

    assert plan["assignment_id"] == ASSIGNMENT_ID
    assert plan["workspace"] == WORKSPACE_PATH
    assert plan["assignment_task"] == "Run the generated validation entry point"
    assert plan["expected_result"] == "A validation result and redacted logs"
    assert wrong_assignment.status_code == 400
    assert wrong_workspace.status_code == 400


def test_suggestions_are_applicable_deterministic_and_never_execute(tmp_path: Path) -> None:
    client, workspace = _setup(tmp_path)
    marker = workspace / "executed.txt"
    (workspace / "tests").mkdir()
    (workspace / "tests" / "test_sample.py").write_text("def test_ok(): assert True\n", encoding="utf-8")
    (workspace / "main.py").write_text(
        "from pathlib import Path\nPath('executed.txt').write_text('ran')\n", encoding="utf-8"
    )
    (workspace / "dashboard").mkdir()
    (workspace / "dashboard" / "app.py").write_text("import streamlit as st\n", encoding="utf-8")
    (workspace / "compose.yml").write_text("services: {}\n", encoding="utf-8")
    with client:
        first = client.get(
            f"/assignments/{ASSIGNMENT_ID}/execution/suggestions",
            params={"workspace_path": WORKSPACE_PATH},
        ).json()
        second = client.get(
            f"/assignments/{ASSIGNMENT_ID}/execution/suggestions",
            params={"workspace_path": WORKSPACE_PATH},
        ).json()

    actions = [(item["action"], item["target"]) for item in first["suggestions"]]
    assert actions == [
        ("pytest", None),
        ("python_script", "main.py"),
        ("streamlit", "dashboard/app.py"),
        ("docker_ps", None),
        ("docker_compose_up", None),
    ]
    assert first == second
    assert first["executed"] is False
    assert all(item["executed"] is False for item in first["suggestions"])
    assert not marker.exists()


def test_success_and_failure_create_persistent_non_completion_evidence(tmp_path: Path) -> None:
    client, workspace = _setup(tmp_path)
    (workspace / "success.py").write_text("print('password=hidden-value')\n", encoding="utf-8")
    (workspace / "failure.py").write_text("raise SystemExit(7)\n", encoding="utf-8")
    with client:
        results = []
        for target in ("success.py", "failure.py"):
            plan = _plan(client, target=target)
            token = _approve(client, plan["plan_id"])
            response = client.post(
                f"/assignments/commands/{plan['plan_id']}/execute",
                json=_association(approval_token=token),
            )
            assert response.status_code == 200, response.text
            results.append(response.json())
        summary = client.get(
            f"/assignments/{ASSIGNMENT_ID}/execution",
            params={"workspace_path": WORKSPACE_PATH},
        ).json()
        logs = client.get(
            f"/assignments/commands/{results[0]['plan_id']}/logs",
            params=_association(),
        ).json()

    assert [result["status"] for result in results] == ["succeeded", "failed"]
    assert sorted(evidence["exit_code"] for evidence in summary["evidence"]) == [0, 7]
    assert all(evidence["academic_completion_inferred"] is False for evidence in summary["evidence"])
    assert all(evidence["audit_record_reference"].startswith("assignment-command:") for evidence in summary["evidence"])
    assert summary["assignment_completion_inferred"] is False
    assert "hidden-value" not in logs["stdout"]
    assert "<redacted>" in logs["stdout"]
    assert {command["display_state"] for command in summary["planned_commands"]} == {"completed", "failed"}


def test_expired_approval_is_mapped_for_frontend_display(tmp_path: Path) -> None:
    client, workspace = _setup(tmp_path)
    (workspace / "main.py").write_text("print('ok')\n", encoding="utf-8")
    with client:
        plan = _plan(client, target="main.py")
        _approve(client, plan["plan_id"])
        record_path = tmp_path / "data" / "assignment_command_runs" / f"{plan['plan_id']}.json"
        record = json.loads(record_path.read_text(encoding="utf-8"))
        record["approval_expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        record_path.write_text(json.dumps(record), encoding="utf-8")
        summary = client.get(
            f"/assignments/{ASSIGNMENT_ID}/execution",
            params={"workspace_path": WORKSPACE_PATH},
        ).json()

    assert summary["approval_state"] == "expired"
    assert summary["execution_state"] == "expired"
    assert summary["planned_commands"][0]["display_state"] == "expired"
