from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.main import create_app


def _create_project(app, root: Path, *, conversation_id: str, workspace_id: str) -> str:
    root.mkdir(parents=True, exist_ok=True)
    project = app.state.canonical_project_service.create_project(
        conversation_id=conversation_id,
        workspace_id=workspace_id,
        repository_root=root,
        repository_root_fingerprint=f"{workspace_id}-root",
        actor_id="local-user",
        idempotency_key=f"create-{workspace_id}",
        folder_authority={
            "status": "completed", "action_id": workspace_id,
            "conversation_id": conversation_id, "workspace_id": workspace_id,
            "repository_root_fingerprint": f"{workspace_id}-root",
        },
        specification={
            "specification_id": f"{workspace_id}-spec", "specification_hash": "1" * 64, "revision": 1,
            "included_paths": ["src"], "excluded_paths": [], "allowed_operations": ["read"],
        },
        manifest={
            "manifest_hash": "2" * 64, "complete": True, "revision": 1,
            "entries": [],
        },
        plan={"revision": 1, "acceptance_criteria": [], "work_units": []},
    )
    return project.project_run_id


def _new_conversation(client: TestClient) -> str:
    response = client.post(
        "/chat/run",
        json={"message": "Hello there, what can you do?", "use_rag": False},
    )
    assert response.status_code == 200
    return response.json()["conversation_id"]


def test_selecting_a_valid_project_persists_and_is_returned_by_hydration(tmp_path: Path) -> None:
    app = create_app(tmp_path / "app.db", workspace_root=tmp_path)
    with TestClient(app) as client:
        conversation_id = _new_conversation(client)
        project_run_id = _create_project(
            app, tmp_path / "repo-a", conversation_id=conversation_id, workspace_id="workspace-a",
        )

        response = client.put(
            f"/chat/conversations/{conversation_id}/active-project",
            json={"project_run_id": project_run_id},
        )
        assert response.status_code == 200
        assert response.json()["active_project_run_id"] == project_run_id

        hydrated = client.get(f"/chat/conversations/{conversation_id}")
    assert hydrated.status_code == 200
    assert hydrated.json()["active_project_run_id"] == project_run_id


def test_clearing_the_selection_persists_null(tmp_path: Path) -> None:
    app = create_app(tmp_path / "app.db", workspace_root=tmp_path)
    with TestClient(app) as client:
        conversation_id = _new_conversation(client)
        project_run_id = _create_project(
            app, tmp_path / "repo-a", conversation_id=conversation_id, workspace_id="workspace-a",
        )
        client.put(
            f"/chat/conversations/{conversation_id}/active-project",
            json={"project_run_id": project_run_id},
        )

        response = client.put(
            f"/chat/conversations/{conversation_id}/active-project",
            json={"project_run_id": None},
        )
        assert response.status_code == 200
        assert response.json()["active_project_run_id"] is None

        hydrated = client.get(f"/chat/conversations/{conversation_id}")
    assert hydrated.json()["active_project_run_id"] is None


def test_switching_between_two_valid_projects_is_deterministic(tmp_path: Path) -> None:
    app = create_app(tmp_path / "app.db", workspace_root=tmp_path)
    with TestClient(app) as client:
        conversation_id = _new_conversation(client)
        project_a = _create_project(
            app, tmp_path / "repo-a", conversation_id=conversation_id, workspace_id="workspace-a",
        )
        project_b = _create_project(
            app, tmp_path / "repo-b", conversation_id=conversation_id, workspace_id="workspace-b",
        )

        client.put(f"/chat/conversations/{conversation_id}/active-project", json={"project_run_id": project_a})
        second = client.put(
            f"/chat/conversations/{conversation_id}/active-project", json={"project_run_id": project_b},
        )
        assert second.json()["active_project_run_id"] == project_b

        hydrated = client.get(f"/chat/conversations/{conversation_id}")
    assert hydrated.json()["active_project_run_id"] == project_b


def test_selection_survives_a_fresh_process_reload(tmp_path: Path) -> None:
    database_path = tmp_path / "app.db"
    app = create_app(database_path, workspace_root=tmp_path)
    with TestClient(app) as client:
        conversation_id = _new_conversation(client)
        project_run_id = _create_project(
            app, tmp_path / "repo-a", conversation_id=conversation_id, workspace_id="workspace-a",
        )
        client.put(
            f"/chat/conversations/{conversation_id}/active-project",
            json={"project_run_id": project_run_id},
        )

    reloaded_app = create_app(database_path, workspace_root=tmp_path)
    with TestClient(reloaded_app) as client:
        hydrated = client.get(f"/chat/conversations/{conversation_id}")
    assert hydrated.status_code == 200
    assert hydrated.json()["active_project_run_id"] == project_run_id


def test_unknown_project_run_id_is_rejected(tmp_path: Path) -> None:
    app = create_app(tmp_path / "app.db", workspace_root=tmp_path)
    with TestClient(app) as client:
        conversation_id = _new_conversation(client)

        response = client.put(
            f"/chat/conversations/{conversation_id}/active-project",
            json={"project_run_id": "does-not-exist"},
        )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "project_not_found"

    with TestClient(app) as client:
        hydrated = client.get(f"/chat/conversations/{conversation_id}")
    assert hydrated.json()["active_project_run_id"] is None


def test_project_bound_to_a_different_conversation_is_rejected(tmp_path: Path) -> None:
    app = create_app(tmp_path / "app.db", workspace_root=tmp_path)
    with TestClient(app) as client:
        conversation_id = _new_conversation(client)
        other_conversation_id = _new_conversation(client)
        project_run_id = _create_project(
            app, tmp_path / "repo-a", conversation_id=other_conversation_id, workspace_id="workspace-a",
        )

        response = client.put(
            f"/chat/conversations/{conversation_id}/active-project",
            json={"project_run_id": project_run_id},
        )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "conversation_mismatch"


def test_selecting_active_project_for_unknown_conversation_returns_404(tmp_path: Path) -> None:
    app = create_app(tmp_path / "app.db", workspace_root=tmp_path)
    with TestClient(app) as client:
        response = client.put(
            "/chat/conversations/no-such-conversation/active-project",
            json={"project_run_id": None},
        )
    assert response.status_code == 404


def test_switching_active_project_does_not_rewrite_an_earlier_requests_lineage(tmp_path: Path) -> None:
    """A later active-project switch must not alter the project binding an
    earlier durable chat request/run was already created with -- the active
    selection is captured per-request at request-creation time (a frontend
    concern that reads this endpoint's stored value), never rewritten
    retroactively by this endpoint, which only ever touches chat_conversations."""
    database_path = tmp_path / "app.db"
    app = create_app(database_path, workspace_root=tmp_path)
    with TestClient(app) as client:
        first = client.post(
            "/chat/run",
            json={"message": "first turn, no project bound", "use_rag": False},
        )
        assert first.status_code == 200
        conversation_id = first.json()["conversation_id"]
        first_run_id = first.json()["run_id"]

        project_run_id = _create_project(
            app, tmp_path / "repo-a", conversation_id=conversation_id, workspace_id="workspace-a",
        )
        client.put(
            f"/chat/conversations/{conversation_id}/active-project",
            json={"project_run_id": project_run_id},
        )

    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        request_row = connection.execute(
            "SELECT request_json FROM chat_requests WHERE run_id = ?", (first_run_id,),
        ).fetchone()
        link_count = connection.execute(
            "SELECT COUNT(*) FROM chat_runtime_links WHERE chat_run_id = ? AND project_run_id IS NOT NULL",
            (first_run_id,),
        ).fetchone()[0]
    assert json.loads(request_row["request_json"])["project_run_id"] is None
    assert link_count == 0


def test_deleting_conversation_removes_the_active_project_selection(tmp_path: Path) -> None:
    app = create_app(tmp_path / "app.db", workspace_root=tmp_path)
    with TestClient(app) as client:
        conversation_id = _new_conversation(client)
        project_run_id = _create_project(
            app, tmp_path / "repo-a", conversation_id=conversation_id, workspace_id="workspace-a",
        )
        client.put(
            f"/chat/conversations/{conversation_id}/active-project",
            json={"project_run_id": project_run_id},
        )

        deleted = client.delete(f"/chat/conversations/{conversation_id}")
        assert deleted.status_code == 200

        hydrated = client.get(f"/chat/conversations/{conversation_id}")
    assert hydrated.status_code == 404
