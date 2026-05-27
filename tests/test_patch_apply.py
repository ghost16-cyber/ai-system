import hashlib
import sqlite3

from fastapi.testclient import TestClient

from backend.app.main import create_app


def test_patch_apply_applies_stored_patch_proposal(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    database_path = tmp_path / "workspace-test.db"

    code = "if flag == True:\n    print(flag)\n"
    target_file = workspace / "bool_patch.py"
    target_file.write_text(code, encoding="utf-8")

    with TestClient(create_app(database_path, workspace_root=workspace)) as test_client:
        analyze_response = test_client.post(
            "/analyze-file",
            json={"path": "bool_patch.py"},
        )

        assert analyze_response.status_code == 200
        analyze_data = analyze_response.json()
        proposal_id = analyze_data["patch_proposals"][0]["proposal_id"]

        apply_response = test_client.post(
            "/patch/apply",
            json={"proposal_id": proposal_id},
        )

        assert apply_response.status_code == 200
        apply_data = apply_response.json()

        assert apply_data["proposal_id"] == proposal_id
        assert apply_data["path"] == "bool_patch.py"
        assert apply_data["status"] == "applied"
        assert apply_data["applied"] is True
        assert apply_data["original_file_sha256"] == hashlib.sha256(
            code.encode("utf-8")
        ).hexdigest()
        assert apply_data["verification"] == {
            "requested": False,
            "tool": None,
            "status": "not_requested",
            "exit_code": None,
            "output": None,
            "checked_at": None,
        }

        assert target_file.read_text(encoding="utf-8") == "if flag:\n    print(flag)\n"

        with sqlite3.connect(database_path) as connection:
            stored_result = connection.execute(
                """
                SELECT
                    status,
                    updated_file_sha256,
                    applied_at,
                    verification_status
                FROM patch_proposals
                WHERE proposal_id = ?
                """,
                (proposal_id,),
            ).fetchone()

        assert stored_result[0] == "applied"
        assert stored_result[1] == apply_data["updated_file_sha256"]
        assert stored_result[2] is not None
        assert stored_result[3] == "not_requested"


def test_patch_apply_rejects_stale_file_hash(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    database_path = tmp_path / "workspace-test.db"

    original_code = "if flag == True:\n    print(flag)\n"
    changed_code = "if flag == False:\n    print(flag)\n"
    target_file = workspace / "bool_patch.py"
    target_file.write_text(original_code, encoding="utf-8")

    with TestClient(create_app(database_path, workspace_root=workspace)) as test_client:
        analyze_response = test_client.post(
            "/analyze-file",
            json={"path": "bool_patch.py"},
        )

        assert analyze_response.status_code == 200
        proposal_id = analyze_response.json()["patch_proposals"][0]["proposal_id"]

        target_file.write_text(changed_code, encoding="utf-8")

        apply_response = test_client.post(
            "/patch/apply",
            json={"proposal_id": proposal_id},
        )

        assert apply_response.status_code == 409
        assert apply_response.json()["detail"] == (
            "Patch target file has changed since the proposal was created."
        )

        assert target_file.read_text(encoding="utf-8") == changed_code

        with sqlite3.connect(database_path) as connection:
            stored_status = connection.execute(
                "SELECT status FROM patch_proposals WHERE proposal_id = ?",
                (proposal_id,),
            ).fetchone()[0]

        assert stored_status == "conflict"


def test_patch_apply_returns_404_for_unknown_proposal(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    database_path = tmp_path / "workspace-test.db"

    with TestClient(create_app(database_path, workspace_root=workspace)) as test_client:
        response = test_client.post(
            "/patch/apply",
            json={"proposal_id": "missing-proposal"},
        )

        assert response.status_code == 404
        assert response.json()["detail"] == "Patch proposal not found."


def test_patch_apply_returns_and_stores_passing_pytest_verification(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    database_path = tmp_path / "workspace-test.db"

    target_file = workspace / "bool_patch.py"
    target_file.write_text("if flag == True:\n    print(flag)\n", encoding="utf-8")
    (workspace / "test_smoke.py").write_text(
        "def test_smoke():\n    assert True\n",
        encoding="utf-8",
    )

    with TestClient(create_app(database_path, workspace_root=workspace)) as test_client:
        proposal_id = test_client.post(
            "/analyze-file",
            json={"path": "bool_patch.py"},
        ).json()["patch_proposals"][0]["proposal_id"]

        response = test_client.post(
            "/patch/apply",
            json={"proposal_id": proposal_id, "run_pytest": True},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "applied"
        assert data["verification"]["requested"] is True
        assert data["verification"]["tool"] == "pytest"
        assert data["verification"]["status"] == "passed"
        assert data["verification"]["exit_code"] == 0
        assert "1 passed" in data["verification"]["output"]
        assert data["verification"]["checked_at"] is not None

        with sqlite3.connect(database_path) as connection:
            stored_verification = connection.execute(
                """
                SELECT verification_status, verification_tool,
                       verification_exit_code, verification_checked_at
                FROM patch_proposals
                WHERE proposal_id = ?
                """,
                (proposal_id,),
            ).fetchone()

        assert stored_verification[0] == "passed"
        assert stored_verification[1] == "pytest"
        assert stored_verification[2] == 0
        assert stored_verification[3] is not None


def test_patch_apply_remains_applied_when_pytest_verification_fails(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    database_path = tmp_path / "workspace-test.db"

    target_file = workspace / "bool_patch.py"
    target_file.write_text("if flag == True:\n    print(flag)\n", encoding="utf-8")
    (workspace / "test_failure.py").write_text(
        "def test_failure():\n    assert False\n",
        encoding="utf-8",
    )

    with TestClient(create_app(database_path, workspace_root=workspace)) as test_client:
        proposal_id = test_client.post(
            "/analyze-file",
            json={"path": "bool_patch.py"},
        ).json()["patch_proposals"][0]["proposal_id"]

        response = test_client.post(
            "/patch/apply",
            json={"proposal_id": proposal_id, "run_pytest": True},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "applied"
        assert data["verification"]["status"] == "failed"
        assert data["verification"]["exit_code"] != 0
        assert "1 failed" in data["verification"]["output"]
        assert target_file.read_text(encoding="utf-8") == "if flag:\n    print(flag)\n"

        with sqlite3.connect(database_path) as connection:
            stored_result = connection.execute(
                """
                SELECT status, verification_status, verification_exit_code
                FROM patch_proposals
                WHERE proposal_id = ?
                """,
                (proposal_id,),
            ).fetchone()

        assert stored_result[0] == "applied"
        assert stored_result[1] == "failed"
        assert stored_result[2] != 0
