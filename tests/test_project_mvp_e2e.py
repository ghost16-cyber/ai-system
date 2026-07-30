from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.project_api import create_project_router
from backend.app.project_artifacts import ProjectArtifactStore
from backend.app.project_control import ProjectControlPlane
from backend.app.project_control.project_service import CanonicalProjectService


def test_canonical_api_journey_never_uses_host_fallback(tmp_path) -> None:
    database = tmp_path / "astra.db"
    root = tmp_path / "workspace"
    root.mkdir()
    artifacts = ProjectArtifactStore(database)
    control = ProjectControlPlane(database, artifact_store=artifacts)
    control.initialize()
    artifacts.initialize()
    app = FastAPI()
    app.include_router(create_project_router(CanonicalProjectService(control, artifacts)))
    client = TestClient(app)
    created = client.post("/chat/projects", json={
        "schema_version": "astra.project-api.create.v1", "conversation_id": "conversation-1",
        "workspace_id": "workspace-1", "repository_root": str(root),
        "repository_root_fingerprint": "root-fingerprint", "idempotency_key": "create-1",
        "folder_authority": {
            "status": "completed", "action_id": "workspace-1", "conversation_id": "conversation-1",
            "workspace_id": "workspace-1", "repository_root_fingerprint": "root-fingerprint",
        },
        "specification": {"specification_hash": "1" * 64, "included_paths": ["app.py"]},
        "manifest": {"manifest_hash": "2" * 64, "complete": True},
        "plan": {"acceptance_criteria": [], "work_units": []},
    }).json()
    project = created["project"]
    cancel = next(item for item in created["next_permitted_actions"] if item["action"] == "cancel_project")
    response = client.post(
        f"/chat/projects/{project['project_run_id']}/actions/cancel_project",
        json={
            "schema_version": "astra.project-api.action.v1", "conversation_id": project["conversation_id"],
            "workspace_id": project["workspace_id"], "actor_id": project["actor_id"],
            "repository_root_fingerprint": project["repository_root_fingerprint"],
            "expected_state_version": cancel["expected_state_version"], "idempotency_key": "cancel-1",
            "plan_revision_id": cancel["plan_revision_id"], "scope_revision_id": cancel["scope_revision_id"],
            "manifest_hash": cancel["manifest_hash"], "artifact_id": None, "artifact_type": None,
            "artifact_hash": None, "artifact_binding_hash": None, "payload": cancel["payload"],
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["project"]["lifecycle_state"] == "cancelled"
    assert response.json()["next_permitted_actions"] == []
