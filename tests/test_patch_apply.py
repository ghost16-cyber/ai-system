import sqlite3

from fastapi.testclient import TestClient

from backend.app.main import create_app


def test_patch_apply_fails_closed_and_mutates_nothing_on_host(tmp_path):
    """R7: /patch/apply writes directly to the host and has been retired.

    A validated patch proposal must be returned as read-only advisory data;
    the separate host-mutating apply step must fail closed unconditionally,
    before the target file or proposal state is ever touched.
    """
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
        proposal_id = analyze_response.json()["patch_proposals"][0]["proposal_id"]

        apply_response = test_client.post(
            "/patch/apply",
            json={"proposal_id": proposal_id},
        )

        assert apply_response.status_code == 503
        detail = apply_response.json()["detail"]
        assert detail["code"] == "legacy_host_execution_retired"

        # No project code was executed on the host: the file is byte-for-byte
        # unchanged, and no application/verification was recorded.
        assert target_file.read_text(encoding="utf-8") == code
        with sqlite3.connect(database_path) as connection:
            stored = connection.execute(
                "SELECT status, applied_at FROM patch_proposals WHERE proposal_id = ?",
                (proposal_id,),
            ).fetchone()
        assert stored[0] == "proposed"
        assert stored[1] is None


def test_patch_apply_fails_closed_even_for_an_unknown_proposal(tmp_path):
    """The retirement check runs before any proposal lookup, so the response
    is a stable fail-closed 503 regardless of the request body."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    database_path = tmp_path / "workspace-test.db"

    with TestClient(create_app(database_path, workspace_root=workspace)) as test_client:
        response = test_client.post(
            "/patch/apply",
            json={"proposal_id": "missing-proposal"},
        )

        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "legacy_host_execution_retired"


def test_patch_apply_fails_closed_with_run_pytest_requested(tmp_path):
    """Requesting pytest verification must not reach the host-execution path
    either: no subprocess is invoked, and the file remains untouched."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    database_path = tmp_path / "workspace-test.db"

    target_file = workspace / "bool_patch.py"
    original = "if flag == True:\n    print(flag)\n"
    target_file.write_text(original, encoding="utf-8")
    (workspace / "test_smoke.py").write_text(
        "def test_smoke():\n    assert True\n", encoding="utf-8",
    )

    with TestClient(create_app(database_path, workspace_root=workspace)) as test_client:
        proposal_id = test_client.post(
            "/analyze-file", json={"path": "bool_patch.py"},
        ).json()["patch_proposals"][0]["proposal_id"]

        response = test_client.post(
            "/patch/apply",
            json={"proposal_id": proposal_id, "run_pytest": True},
        )

        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "legacy_host_execution_retired"
        assert target_file.read_text(encoding="utf-8") == original
        with sqlite3.connect(database_path) as connection:
            stored = connection.execute(
                "SELECT verification_status FROM patch_proposals WHERE proposal_id = ?",
                (proposal_id,),
            ).fetchone()
        assert stored[0] == "not_requested"
