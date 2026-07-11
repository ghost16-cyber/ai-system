from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.main import create_app


def _client(tmp_path: Path) -> tuple[TestClient, Path]:
    workspace = tmp_path / "assignment_workspaces" / "assignment_2"
    workspace.mkdir(parents=True)
    return TestClient(create_app(tmp_path / "app.db", workspace_root=tmp_path)), workspace


def _plan(client: TestClient, workspace: Path, **overrides) -> dict:
    payload = {
        "action": "python_script",
        "workspace_path": str(workspace.relative_to(workspace.parents[1])),
        "target": "safe.py",
        "timeout_seconds": 10,
        **overrides,
    }
    response = client.post("/assignments/commands/plan", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def _approve(client: TestClient, plan_id: str) -> str:
    response = client.post(
        f"/assignments/commands/{plan_id}/approve",
        json={"confirmation": f"APPROVE {plan_id}"},
    )
    assert response.status_code == 200, response.text
    return response.json()["approval_token"]


def test_planning_is_non_executing_and_persists_audit_record(tmp_path: Path) -> None:
    client, workspace = _client(tmp_path)
    marker = workspace / "marker.txt"
    (workspace / "safe.py").write_text(
        "from pathlib import Path\nPath('marker.txt').write_text('ran')\n",
        encoding="utf-8",
    )
    with client:
        plan = _plan(client, workspace)
        status = client.get(f"/assignments/commands/{plan['plan_id']}")

    assert plan["status"] == "planned"
    assert plan["approval_required"] is True
    assert plan["shell_used"] is False
    assert "approval_token_hash" not in plan
    assert status.status_code == 200
    assert not marker.exists()
    assert (tmp_path / "data" / "assignment_command_runs" / f"{plan['plan_id']}.json").is_file()


def test_unknown_action_and_missing_target_are_rejected(tmp_path: Path) -> None:
    client, workspace = _client(tmp_path)
    relative = str(workspace.relative_to(tmp_path))
    with client:
        unknown = client.post(
            "/assignments/commands/plan",
            json={"action": "bash", "workspace_path": relative},
        )
        missing = client.post(
            "/assignments/commands/plan",
            json={
                "action": "python_script",
                "workspace_path": relative,
                "target": "missing.py",
            },
        )

    assert unknown.status_code == 400
    assert missing.status_code == 400


def test_target_traversal_and_symlink_are_rejected(tmp_path: Path) -> None:
    client, workspace = _client(tmp_path)
    outside = tmp_path / "outside.py"
    outside.write_text("print('outside')\n", encoding="utf-8")
    (workspace / "linked.py").symlink_to(outside)
    relative = str(workspace.relative_to(tmp_path))
    with client:
        traversal = client.post(
            "/assignments/commands/plan",
            json={
                "action": "python_script",
                "workspace_path": relative,
                "target": "../outside.py",
            },
        )
        symlink = client.post(
            "/assignments/commands/plan",
            json={
                "action": "python_script",
                "workspace_path": relative,
                "target": "linked.py",
            },
        )

    assert traversal.status_code == 400
    assert symlink.status_code == 400


def test_exact_approval_is_required_and_token_is_not_persisted(tmp_path: Path) -> None:
    client, workspace = _client(tmp_path)
    (workspace / "safe.py").write_text("print('ok')\n", encoding="utf-8")
    with client:
        plan = _plan(client, workspace)
        rejected = client.post(
            f"/assignments/commands/{plan['plan_id']}/approve",
            json={"confirmation": "yes"},
        )
        token = _approve(client, plan["plan_id"])

    stored = json.loads(
        (tmp_path / "data" / "assignment_command_runs" / f"{plan['plan_id']}.json").read_text(encoding="utf-8")
    )
    assert rejected.status_code == 400
    assert token
    assert token not in json.dumps(stored)
    assert stored["approval_token_hash"]


def test_execute_requires_valid_approval_and_runs_once(tmp_path: Path) -> None:
    client, workspace = _client(tmp_path)
    (workspace / "safe.py").write_text("print('approved run')\n", encoding="utf-8")
    with client:
        plan = _plan(client, workspace)
        before_approval = client.post(
            f"/assignments/commands/{plan['plan_id']}/execute",
            json={"approval_token": "not-approved"},
        )
        token = _approve(client, plan["plan_id"])
        wrong_token = client.post(
            f"/assignments/commands/{plan['plan_id']}/execute",
            json={"approval_token": "wrong-token"},
        )
        executed = client.post(
            f"/assignments/commands/{plan['plan_id']}/execute",
            json={"approval_token": token},
        )
        logs = client.get(f"/assignments/commands/{plan['plan_id']}/logs")
        repeated = client.post(
            f"/assignments/commands/{plan['plan_id']}/execute",
            json={"approval_token": token},
        )

    assert before_approval.status_code == 400
    assert wrong_token.status_code == 400
    assert executed.status_code == 200
    assert executed.json()["status"] == "succeeded"
    assert executed.json()["exit_code"] == 0
    assert "approved run" in executed.json()["stdout"]
    assert logs.status_code == 200
    assert logs.json()["stdout"] == executed.json()["stdout"]
    assert logs.json()["status"] == "succeeded"
    assert repeated.status_code == 400


def test_execution_strips_secret_environment_and_redacts_logs(tmp_path: Path, monkeypatch) -> None:
    client, workspace = _client(tmp_path)
    monkeypatch.setenv("SNOWFLAKE_PASSWORD", "environment-secret")
    (workspace / "safe.py").write_text(
        "import os\n"
        "print('env=' + str(os.getenv('SNOWFLAKE_PASSWORD')))\n"
        "print('password=visible-secret')\n"
        "print('token: sk-proj-abcdefghijklmnopqrstuvwxyz')\n",
        encoding="utf-8",
    )
    with client:
        plan = _plan(client, workspace)
        token = _approve(client, plan["plan_id"])
        result = client.post(
            f"/assignments/commands/{plan['plan_id']}/execute",
            json={"approval_token": token},
        ).json()

    assert "environment-secret" not in result["stdout"]
    assert "visible-secret" not in result["stdout"]
    assert "sk-proj" not in result["stdout"]
    assert "env=None" in result["stdout"]
    assert "<redacted>" in result["stdout"]


def test_timeout_terminates_command_and_captures_partial_log(tmp_path: Path) -> None:
    client, workspace = _client(tmp_path)
    (workspace / "slow.py").write_text(
        "import time\nprint('started', flush=True)\ntime.sleep(30)\n",
        encoding="utf-8",
    )
    with client:
        plan = _plan(client, workspace, target="slow.py", timeout_seconds=1)
        token = _approve(client, plan["plan_id"])
        response = client.post(
            f"/assignments/commands/{plan['plan_id']}/execute",
            json={"approval_token": token},
        )

    assert response.status_code == 200
    assert response.json()["status"] == "timed_out"
    assert response.json()["timed_out"] is True
    assert "started" in response.json()["stdout"]


def test_plan_integrity_tampering_blocks_approval(tmp_path: Path) -> None:
    client, workspace = _client(tmp_path)
    (workspace / "safe.py").write_text("print('ok')\n", encoding="utf-8")
    with client:
        plan = _plan(client, workspace)
        record_path = tmp_path / "data" / "assignment_command_runs" / f"{plan['plan_id']}.json"
        record = json.loads(record_path.read_text(encoding="utf-8"))
        record["argv"] = ["python", "other.py"]
        record_path.write_text(json.dumps(record), encoding="utf-8")
        response = client.post(
            f"/assignments/commands/{plan['plan_id']}/approve",
            json={"confirmation": f"APPROVE {plan['plan_id']}"},
        )

    assert response.status_code == 400
    assert "integrity" in response.json()["detail"].lower()


def test_script_changed_after_approval_is_not_executed(tmp_path: Path) -> None:
    client, workspace = _client(tmp_path)
    marker = workspace / "marker.txt"
    script = workspace / "safe.py"
    script.write_text("print('reviewed')\n", encoding="utf-8")
    with client:
        plan = _plan(client, workspace)
        token = _approve(client, plan["plan_id"])
        script.write_text(
            "from pathlib import Path\nPath('marker.txt').write_text('changed')\n",
            encoding="utf-8",
        )
        response = client.post(
            f"/assignments/commands/{plan['plan_id']}/execute",
            json={"approval_token": token},
        )

    assert response.status_code == 400
    assert "changed after planning" in response.json()["detail"]
    assert not marker.exists()


def test_docker_compose_plan_requires_workspace_compose_file(tmp_path: Path) -> None:
    client, workspace = _client(tmp_path)
    relative = str(workspace.relative_to(tmp_path))
    with client:
        missing = client.post(
            "/assignments/commands/plan",
            json={"action": "docker_compose_up", "workspace_path": relative},
        )
        (workspace / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
        planned = client.post(
            "/assignments/commands/plan",
            json={"action": "docker_compose_up", "workspace_path": relative},
        )

    assert missing.status_code == 400
    assert planned.status_code == 200
    assert planned.json()["risk_level"] == "medium"
    assert planned.json()["status"] == "planned"


def test_outside_workspace_and_invalid_plan_id_are_controlled(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    outside = tmp_path.parent / "outside-assignment-command"
    outside.mkdir(exist_ok=True)
    with client:
        outside_response = client.post(
            "/assignments/commands/plan",
            json={"action": "pytest", "workspace_path": str(outside)},
        )
        invalid_id = client.get("/assignments/commands/not-a-plan-id")

    assert outside_response.status_code == 400
    assert invalid_id.status_code == 400


def test_malformed_persisted_plan_returns_controlled_error(tmp_path: Path) -> None:
    client, workspace = _client(tmp_path)
    (workspace / "safe.py").write_text("print('ok')\n", encoding="utf-8")
    with client:
        plan = _plan(client, workspace)
        record_path = tmp_path / "data" / "assignment_command_runs" / f"{plan['plan_id']}.json"
        record_path.write_text("{not-json", encoding="utf-8")
        response = client.get(f"/assignments/commands/{plan['plan_id']}")

    assert response.status_code == 400
    assert "malformed" in response.json()["detail"].lower()
