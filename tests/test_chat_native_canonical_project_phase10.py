from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.main import create_app


TASK = "Deliver the project change in README.md by implementing app.py. Verify it with pytest."


def _project(root: Path) -> Path:
    project = root / "delivery_project"
    project.mkdir()
    (project / "README.md").write_text("Feature: greet returns Hello, Ada!\n", encoding="utf-8")
    (project / "app.py").write_text(
        "def greet(name):\n    raise NotImplementedError\n", encoding="utf-8",
    )
    (project / "test_app.py").write_text(
        "from app import greet\n\ndef test_greet():\n    assert greet('Ada') == 'Hello, Ada!'\n",
        encoding="utf-8",
    )
    return project


def _connect(client: TestClient, project: Path) -> str:
    requested = client.post("/chat/run", json={"message": f"Use {project}", "use_rag": True}).json()
    client.post(
        f"/chat/folders/{requested['action']['action_id']}/approve",
        json={"chat_run_id": requested["run_id"]},
    )
    return requested["conversation_id"]


def test_exact_stream_replay_of_a_delivery_request_creates_only_one_project(tmp_path: Path) -> None:
    project = _project(tmp_path)
    with TestClient(create_app(tmp_path / "app.db", tmp_path)) as client:
        conversation_id = _connect(client, project)
        body = {"message": TASK, "conversation_id": conversation_id, "use_rag": True}
        pending = client.post("/chat/requests", json=body).json()
        request_id = pending["request_id"]

        first = client.post("/chat/stream", json={**body, "request_id": request_id})
        assert first.status_code == 200
        first_events = [json.loads(line) for line in first.text.splitlines() if line.strip()]
        first_run = next(event for event in first_events if event["event"] == "run_completed")["data"]["run"]
        first_project_run_id = first_run["action"]["project"]["project_run_id"]

        second = client.post("/chat/stream", json={**body, "request_id": request_id})
        assert second.status_code == 200
        second_events = [json.loads(line) for line in second.text.splitlines() if line.strip()]
        second_run = next(event for event in second_events if event["event"] == "run_completed")["data"]["run"]

    assert second_run["action"]["project"]["project_run_id"] == first_project_run_id
    assert second_run["run_id"] == first_run["run_id"]


def test_a_second_delivery_request_while_one_is_active_does_not_create_a_second_project(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    with TestClient(create_app(tmp_path / "app.db", tmp_path)) as client:
        conversation_id = _connect(client, project)
        first = client.post("/chat/run", json={
            "message": TASK, "conversation_id": conversation_id, "use_rag": True,
        }).json()
        first_project_run_id = first["action"]["project"]["project_run_id"]

        second = client.post("/chat/run", json={
            "message": "Deliver another unrelated change to fix a different bug.",
            "conversation_id": conversation_id, "use_rag": True,
        }).json()

    assert second["action"]["action_type"] == "canonical_project"
    assert second["action"]["project"]["project_run_id"] == first_project_run_id
    assert "already active" in second["assistant_response"]


def test_a_completed_terminal_project_does_not_block_a_new_delivery_request(tmp_path: Path) -> None:
    project = _project(tmp_path)
    with TestClient(create_app(tmp_path / "app.db", tmp_path)) as client:
        conversation_id = _connect(client, project)
        first = client.post("/chat/run", json={
            "message": TASK, "conversation_id": conversation_id, "use_rag": True,
        }).json()
        first_project_run_id = first["action"]["project"]["project_run_id"]
        cancel_action = next(
            item for item in first["action"]["next_permitted_actions"] if item["action"] == "cancel_project"
        )
        cancelled = client.post(
            f"/chat/projects/{first_project_run_id}/actions/cancel_project",
            json={
                "conversation_id": conversation_id, "workspace_id": first["action"]["project"]["workspace_id"],
                "actor_id": "local-user",
                "repository_root_fingerprint": first["action"]["project"]["repository_root_fingerprint"],
                "expected_state_version": cancel_action["expected_state_version"],
                "idempotency_key": "cancel-for-test",
                "plan_revision_id": cancel_action["plan_revision_id"],
                "scope_revision_id": cancel_action["scope_revision_id"],
                "manifest_hash": cancel_action["manifest_hash"],
                "artifact_id": cancel_action["artifact_id"], "artifact_type": cancel_action["artifact_type"],
                "artifact_hash": cancel_action["artifact_hash"],
                "artifact_binding_hash": cancel_action["artifact_binding_hash"],
                "payload": cancel_action["payload"],
            },
        )
        assert cancelled.status_code == 200, cancelled.text

        second = client.post("/chat/run", json={
            "message": TASK, "conversation_id": conversation_id, "use_rag": True,
        }).json()

    assert second["action"]["action_type"] == "canonical_project"
    assert second["action"]["project"]["project_run_id"] != first_project_run_id


def test_ordinary_chat_wording_is_never_treated_as_project_approval(tmp_path: Path) -> None:
    project = _project(tmp_path)
    with TestClient(create_app(tmp_path / "app.db", tmp_path)) as client:
        conversation_id = _connect(client, project)
        created = client.post("/chat/run", json={
            "message": TASK, "conversation_id": conversation_id, "use_rag": True,
        }).json()
        project_run_id = created["action"]["project"]["project_run_id"]
        state_version_before = created["action"]["project"]["state_version"]

        client.post("/chat/run", json={
            "message": "Yes, I approve the plan. Please go ahead and apply it.",
            "conversation_id": conversation_id, "use_rag": False,
        })

        current = client.get(f"/chat/projects/{project_run_id}")

    assert current.status_code == 200
    assert current.json()["project"]["state_version"] == state_version_before
    assert current.json()["project"]["pending_user_action"] == "approve_plan"


def test_events_endpoint_requires_an_existing_project(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path / "app.db", tmp_path)) as client:
        response = client.get("/chat/projects/does-not-exist/events")
    assert response.status_code == 404


def test_events_endpoint_paginates_and_never_exposes_raw_event_payloads(tmp_path: Path) -> None:
    project = _project(tmp_path)
    with TestClient(create_app(tmp_path / "app.db", tmp_path)) as client:
        conversation_id = _connect(client, project)
        created = client.post("/chat/run", json={
            "message": TASK, "conversation_id": conversation_id, "use_rag": True,
        }).json()
        project_run_id = created["action"]["project"]["project_run_id"]

        first_page = client.get(f"/chat/projects/{project_run_id}/events", params={"limit": 2})
        assert first_page.status_code == 200
        first_body = first_page.json()
        assert first_body["schema_version"] == "astra.project-api.events.v1"
        assert first_body["project_run_id"] == project_run_id
        assert len(first_body["items"]) == 2
        assert first_body["next_after_sequence"] == first_body["items"][-1]["sequence"]

        second_page = client.get(
            f"/chat/projects/{project_run_id}/events",
            params={"limit": 2, "after_sequence": first_body["next_after_sequence"]},
        )
        second_body = second_page.json()

    all_sequences = [item["sequence"] for item in first_body["items"] + second_body["items"]]
    assert all_sequences == sorted(all_sequences)
    assert len(set(all_sequences)) == len(all_sequences)
    for item in first_body["items"] + second_body["items"]:
        assert set(item.keys()) == {"schema_version", "sequence", "event_type", "label", "occurred_at"}
        assert item["label"]
        assert "event_json" not in item
        assert "metadata" not in item
        assert "payload" not in item
