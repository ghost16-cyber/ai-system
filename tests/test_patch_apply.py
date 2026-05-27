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

        assert target_file.read_text(encoding="utf-8") == "if flag:\n    print(flag)\n"

        with sqlite3.connect(database_path) as connection:
            stored_status = connection.execute(
                "SELECT status FROM patch_proposals WHERE proposal_id = ?",
                (proposal_id,),
            ).fetchone()[0]

        assert stored_status == "applied"


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