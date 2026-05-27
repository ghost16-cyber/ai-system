import hashlib
import sqlite3

from fastapi.testclient import TestClient

from backend.app.main import create_app


def test_patch_preview_returns_unified_diff_without_writing_file(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    database_path = tmp_path / "workspace-test.db"

    code = "if flag == True:\n    print(flag)\n"
    target_file = workspace / "bool_patch.py"
    target_file.write_text(code, encoding="utf-8")

    with TestClient(create_app(database_path, workspace_root=workspace)) as test_client:
        proposal_id = test_client.post(
            "/analyze-file",
            json={"path": "bool_patch.py"},
        ).json()["patch_proposals"][0]["proposal_id"]

        response = test_client.post(
            "/patch/preview",
            json={"proposal_id": proposal_id},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["proposal_id"] == proposal_id
        assert data["path"] == "bool_patch.py"
        assert data["status"] == "proposed"
        assert data["preview_available"] is True
        assert data["original_file_sha256"] == hashlib.sha256(
            code.encode("utf-8")
        ).hexdigest()
        assert data["current_file_sha256"] == data["original_file_sha256"]
        assert data["unified_diff"] == (
            "--- a/bool_patch.py\n"
            "+++ b/bool_patch.py\n"
            "@@ -1,2 +1,2 @@\n"
            "-if flag == True:\n"
            "+if flag:\n"
            "     print(flag)\n"
        )

        assert target_file.read_text(encoding="utf-8") == code
        with sqlite3.connect(database_path) as connection:
            stored_status = connection.execute(
                "SELECT status FROM patch_proposals WHERE proposal_id = ?",
                (proposal_id,),
            ).fetchone()[0]

        assert stored_status == "proposed"


def test_patch_preview_reports_stale_file_without_mutating_proposal_status(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    database_path = tmp_path / "workspace-test.db"

    original_code = "if flag == True:\n    print(flag)\n"
    current_code = "if flag == False:\n    print(flag)\n"
    target_file = workspace / "bool_patch.py"
    target_file.write_text(original_code, encoding="utf-8")

    with TestClient(create_app(database_path, workspace_root=workspace)) as test_client:
        proposal_id = test_client.post(
            "/analyze-file",
            json={"path": "bool_patch.py"},
        ).json()["patch_proposals"][0]["proposal_id"]
        target_file.write_text(current_code, encoding="utf-8")

        response = test_client.post(
            "/patch/preview",
            json={"proposal_id": proposal_id},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "proposed"
        assert data["preview_available"] is False
        assert data["unified_diff"] is None
        assert data["original_file_sha256"] != data["current_file_sha256"]
        assert data["current_file_sha256"] == hashlib.sha256(
            current_code.encode("utf-8")
        ).hexdigest()
        assert target_file.read_text(encoding="utf-8") == current_code

        with sqlite3.connect(database_path) as connection:
            stored_status = connection.execute(
                "SELECT status FROM patch_proposals WHERE proposal_id = ?",
                (proposal_id,),
            ).fetchone()[0]

        assert stored_status == "proposed"


def test_patch_preview_rejects_applied_proposal(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    database_path = tmp_path / "workspace-test.db"
    (workspace / "bool_patch.py").write_text(
        "if flag == True:\n    print(flag)\n",
        encoding="utf-8",
    )

    with TestClient(create_app(database_path, workspace_root=workspace)) as test_client:
        proposal_id = test_client.post(
            "/analyze-file",
            json={"path": "bool_patch.py"},
        ).json()["patch_proposals"][0]["proposal_id"]
        applied = test_client.post("/patch/apply", json={"proposal_id": proposal_id})
        preview = test_client.post("/patch/preview", json={"proposal_id": proposal_id})

        assert applied.status_code == 200
        assert preview.status_code == 400
        assert preview.json()["detail"] == (
            "Patch proposal is not previewable in status: applied"
        )
