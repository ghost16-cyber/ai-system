import hashlib
import sqlite3

from fastapi.testclient import TestClient

from backend.app.main import create_app


def test_analyze_file_creates_boolean_comparison_patch_proposal(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    database_path = tmp_path / "workspace-test.db"
    code = "if flag == True:\n    print(flag)\n"
    (workspace / "bool_patch.py").write_text(code, encoding="utf-8")

    with TestClient(
        create_app(database_path, workspace_root=workspace)
    ) as test_client:
        response = test_client.post("/analyze-file", json={"path": "bool_patch.py"})

        assert response.status_code == 200
        data = response.json()

        assert data["filename"] == "bool_patch.py"
        assert [issue["rule_id"] for issue in data["issues"]] == [
            "redundant_boolean_comparison"
        ]
        assert data["issues"][0]["validation"]["status"] == "passed"
        assert data["issues"][0]["suggested_code"] == "if flag:\n    print(flag)\n"
        assert data["patch_proposals"] == [
            {
                "proposal_id": data["patch_proposals"][0]["proposal_id"],
                "analysis_id": data["analysis_id"],
                "finding_id": data["issues"][0]["finding_id"],
                "path": "bool_patch.py",
                "original_file_sha256": hashlib.sha256(code.encode()).hexdigest(),
                "start_line": 1,
                "end_line": 1,
                "replacement": "if flag:\n",
                "validation_status": "passed",
                "status": "proposed",
            }
        ]
        assert data["metadata"]["validated_fix_count"] == 1
        assert data["metadata"]["code_stored"] is False

        with sqlite3.connect(database_path) as connection:
            stored = connection.execute(
                "SELECT path, replacement, original_file_sha256 FROM patch_proposals"
            ).fetchone()

        assert stored == (
            "bool_patch.py",
            "if flag:\n",
            hashlib.sha256(code.encode()).hexdigest(),
        )
        assert code not in stored[1]
