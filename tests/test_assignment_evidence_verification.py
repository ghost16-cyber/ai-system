from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.assignments.verification import build_workspace_evidence_inventory
from backend.app.main import create_app


ASSIGNMENT_ID = "assignment-2"
WORKSPACE_PATH = "assignment_workspaces/assignment_2"


def _setup(tmp_path: Path) -> tuple[TestClient, Path]:
    workspace = tmp_path / WORKSPACE_PATH
    workspace.mkdir(parents=True)
    return TestClient(create_app(tmp_path / "app.db", workspace_root=tmp_path)), workspace


def _requirement(
    requirement_id: str,
    *,
    category: str = "code_file",
    deliverable: str = "python",
    expected: list[str] | None = None,
    title: str | None = None,
) -> dict:
    return {
        "requirement_id": requirement_id,
        "title": title or requirement_id.replace("-", " ").title(),
        "description": title or requirement_id,
        "source_reference": "brief:task-1",
        "requirement_category": category,
        "required_deliverable_type": deliverable,
        "expected_evidence": expected or [],
        "verification_method": "deterministic_test_rule",
        "task_id": requirement_id,
    }


def _verify(client: TestClient, requirements: list[dict], assignment_id: str = ASSIGNMENT_ID):
    return client.post(
        f"/assignments/{assignment_id}/verify",
        json={"workspace_path": WORKSPACE_PATH, "assignment_output": {"requirements": requirements}},
    )


def _write_command(
    tmp_path: Path,
    workspace: Path,
    *,
    plan_id: str,
    status: str,
    exit_code: int,
    finished_at: datetime,
) -> None:
    store = tmp_path / "data" / "assignment_command_runs"
    store.mkdir(parents=True, exist_ok=True)
    (store / f"{plan_id}.json").write_text(json.dumps({
        "schema_version": 2,
        "plan_id": plan_id,
        "assignment_id": ASSIGNMENT_ID,
        "assignment_task": "tests requirement",
        "action": "pytest",
        "status": status,
        "exit_code": exit_code,
        "workspace_path": str(workspace.resolve()),
        "finished_at": finished_at.isoformat(),
    }), encoding="utf-8")


def test_inventory_records_relative_paths_types_hashes_and_empty_warnings(tmp_path: Path) -> None:
    _, workspace = _setup(tmp_path)
    (workspace / "main.py").write_text("print('assignment evidence')\n", encoding="utf-8")
    (workspace / "tests").mkdir()
    (workspace / "tests" / "test_main.py").write_text("", encoding="utf-8")
    (workspace / "report.md").write_text("# Report\n", encoding="utf-8")
    inventory = build_workspace_evidence_inventory(tmp_path, workspace)

    by_path = {item.relative_path: item for item in inventory}
    assert set(by_path) == {"main.py", "report.md", "tests/test_main.py"}
    assert by_path["main.py"].file_type == "python"
    assert by_path["tests/test_main.py"].file_type == "python_test"
    assert by_path["tests/test_main.py"].warnings == ["File is empty."]
    assert len(by_path["main.py"].sha256) == 64
    assert all(not Path(item.relative_path).is_absolute() for item in inventory)
    assert all(str(tmp_path) not in item.relative_path for item in inventory)


def test_inventory_rejects_symlink_and_endpoint_rejects_traversal(tmp_path: Path) -> None:
    client, workspace = _setup(tmp_path)
    outside = tmp_path / "outside.py"
    outside.write_text("print('outside')\n", encoding="utf-8")
    (workspace / "linked.py").symlink_to(outside)
    with pytest.raises(ValueError, match="symlink"):
        build_workspace_evidence_inventory(tmp_path, workspace)
    with client:
        response = client.post(
            f"/assignments/{ASSIGNMENT_ID}/verify",
            json={"workspace_path": "../outside", "assignment_output": {"requirements": [_requirement("code")]}},
        )
    assert response.status_code == 400


def test_missing_and_empty_required_evidence_are_not_verified(tmp_path: Path) -> None:
    client, workspace = _setup(tmp_path)
    (workspace / "empty.py").write_text("", encoding="utf-8")
    with client:
        response = _verify(client, [
            _requirement("missing-code", expected=["missing.py"]),
            _requirement("empty-code", expected=["empty.py"]),
        ])
    assert response.status_code == 200, response.text
    statuses = {item["requirement_id"]: item["status"] for item in response.json()["requirements"]}
    assert statuses == {"missing-code": "missing", "empty-code": "failed"}
    assert response.json()["readiness"]["academic_completion_inferred"] is False


def test_passing_failed_stale_and_conflicting_test_evidence(tmp_path: Path) -> None:
    client, workspace = _setup(tmp_path)
    test_file = workspace / "tests" / "test_app.py"
    test_file.parent.mkdir()
    test_file.write_text("def test_ok(): assert True\n", encoding="utf-8")
    now = datetime.now(timezone.utc)
    _write_command(tmp_path, workspace, plan_id="a" * 32, status="succeeded", exit_code=0, finished_at=now + timedelta(seconds=5))
    with client:
        passed = _verify(client, [_requirement("tests", category="testing", deliverable="test_suite", expected=["tests/test_app.py"])])
    assert passed.json()["requirements"][0]["status"] == "verified"

    test_file.write_text("def test_changed(): assert True\n", encoding="utf-8")
    future = now + timedelta(seconds=20)
    timestamp = future.timestamp()
    test_file.touch()
    import os
    os.utime(test_file, (timestamp, timestamp))
    with client:
        stale = _verify(client, [_requirement("tests", category="testing", deliverable="test_suite", expected=["tests/test_app.py"])])
    assert stale.json()["requirements"][0]["status"] == "partially_verified"
    assert "stale" in " ".join(stale.json()["requirements"][0]["warnings"]).lower()

    _write_command(tmp_path, workspace, plan_id="b" * 32, status="failed", exit_code=1, finished_at=future)
    with client:
        conflicting = _verify(client, [_requirement("tests", category="testing", deliverable="test_suite", expected=["tests/test_app.py"])])
    assert conflicting.json()["requirements"][0]["status"] == "requires_manual_review"
    assert "conflicting" in " ".join(conflicting.json()["requirements"][0]["warnings"]).lower()


def test_failed_execution_is_linked_and_remains_visible(tmp_path: Path) -> None:
    client, workspace = _setup(tmp_path)
    (workspace / "tests").mkdir()
    (workspace / "tests" / "test_app.py").write_text("def test_bad(): assert False\n", encoding="utf-8")
    _write_command(tmp_path, workspace, plan_id="c" * 32, status="failed", exit_code=2, finished_at=datetime.now(timezone.utc))
    with client:
        body = _verify(client, [_requirement("tests", category="testing", deliverable="test_suite")]).json()
    requirement = body["requirements"][0]
    assert requirement["status"] == "failed"
    assert requirement["linked_execution_evidence"] == [f"assignment-command:{'c' * 32}"]


def test_screenshot_and_report_require_explicit_manual_review(tmp_path: Path) -> None:
    client, workspace = _setup(tmp_path)
    (workspace / "dashboard.png").write_bytes(b"not-a-real-image-but-present")
    (workspace / "report.md").write_text("# Findings\nObserved evidence.\n", encoding="utf-8")
    requirements = [
        _requirement("screenshot", category="screenshot", deliverable="image", expected=["dashboard.png"]),
        _requirement("report", category="report", deliverable="report", expected=["report.md"]),
    ]
    with client:
        first = _verify(client, requirements).json()
        screenshot = first["requirements"][0]
        accepted = client.post(
            f"/assignments/{ASSIGNMENT_ID}/evidence/review",
            json={
                "workspace_path": WORKSPACE_PATH,
                "requirement_id": "screenshot",
                "evidence_reference": "file:dashboard.png",
                "decision": "accepted",
                "note": "Reviewed dashboard; password=review-secret",
            },
        )
        persisted_acceptance = client.get(
            f"/assignments/{ASSIGNMENT_ID}/evidence",
            params={"workspace_path": WORKSPACE_PATH},
        ).json()
        after_acceptance = _verify(client, requirements).json()
        rejected = client.post(
            f"/assignments/{ASSIGNMENT_ID}/evidence/review",
            json={
                "workspace_path": WORKSPACE_PATH,
                "requirement_id": "screenshot",
                "evidence_reference": "file:dashboard.png",
                "decision": "rejected",
                "note": "Screenshot is unreadable.",
            },
        )
        after_rejection = _verify(client, requirements).json()

    assert [item["status"] for item in first["requirements"]] == ["requires_manual_review", "requires_manual_review"]
    assert screenshot["linked_workspace_files"] == ["dashboard.png"]
    assert accepted.status_code == 200
    assert "review-secret" not in accepted.text
    assert "<redacted>" in accepted.text
    assert persisted_acceptance["requirements"][0]["status"] == "verified"
    assert after_acceptance["requirements"][0]["status"] == "verified"
    assert rejected.status_code == 200
    assert after_rejection["requirements"][0]["status"] == "failed"
    assert after_rejection["requirements"][1]["status"] == "requires_manual_review"
    assert len(after_rejection["manual_reviews"]) == 2


def test_corrupt_snapshot_and_cross_assignment_access_fail_safely(tmp_path: Path) -> None:
    client, workspace = _setup(tmp_path)
    (workspace / "main.py").write_text("def main():\n    print('assignment implementation')\n", encoding="utf-8")
    with client:
        initial = _verify(client, [_requirement("code", expected=["main.py"])]).json()
        cross = client.get(
            "/assignments/assignment-3/evidence",
            params={"workspace_path": WORKSPACE_PATH},
        )
        snapshot = next((tmp_path / "data" / "assignment_verification" / "snapshots").glob("*.json"))
        snapshot.write_text("{corrupt", encoding="utf-8")
        corrupt = client.get(
            f"/assignments/{ASSIGNMENT_ID}/readiness",
            params={"workspace_path": WORKSPACE_PATH},
        )
    assert initial["requirements"][0]["status"] == "detected"
    assert initial["readiness"]["academic_completion_inferred"] is False
    assert cross.status_code == 404
    assert corrupt.status_code == 400
    assert "corrupt" in corrupt.json()["detail"].lower()


def test_verification_never_calls_subprocess_or_accepts_evidence_automatically(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client, workspace = _setup(tmp_path)
    (workspace / "dashboard.png").write_bytes(b"present screenshot evidence bytes")

    def forbidden(*args, **kwargs):
        raise AssertionError("verification must not invoke subprocess")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    with client:
        body = _verify(client, [_requirement("screenshot", category="screenshot", deliverable="image", expected=["dashboard.png"])]).json()
    assert body["requirements"][0]["status"] == "requires_manual_review"
    assert body["manual_reviews"] == []
