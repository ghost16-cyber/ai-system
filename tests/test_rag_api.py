from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.project_retrieval.routes import create_project_retrieval_router
from tests.test_rag_integration import _fixture, _ingest, _request


def test_project_scoped_rag_routes_are_typed_and_read_only(tmp_path) -> None:
    _root, _source, _database, _control, _artifacts, retrieval, binding = _fixture(tmp_path)
    _ingest(retrieval, binding)
    application = FastAPI()
    application.include_router(create_project_retrieval_router(retrieval))
    client = TestClient(application)
    project_id = str(binding["project_id"])

    response = client.post(
        f"/chat/projects/{project_id}/rag/retrieve",
        json=_request(binding).model_dump(mode="json"),
    )
    assert response.status_code == 200
    artifact = response.json()
    assert artifact["advisory_only"] is True
    assert artifact["has_execution_authority"] is False

    status = client.get(f"/chat/projects/{project_id}/rag/status")
    assert status.status_code == 200
    assert status.json()["active_chunk_count"] >= 1
    listed = client.get(f"/chat/projects/{project_id}/rag/artifacts")
    assert listed.status_code == 200
    assert listed.json()["count"] == 1
    loaded = client.get(
        f"/chat/projects/{project_id}/rag/artifacts/{artifact['artifact_id']}"
    )
    assert loaded.status_code == 200
    assert loaded.json()["artifact_hash"] == artifact["artifact_hash"]


def test_route_rejects_cross_project_body_binding(tmp_path) -> None:
    _root, _source, _database, _control, _artifacts, retrieval, binding = _fixture(tmp_path)
    application = FastAPI()
    application.include_router(create_project_retrieval_router(retrieval))
    response = TestClient(application).post(
        "/chat/projects/another-project/rag/ingest",
        json={
            **binding,
            "idempotency_key": "ingest-cross-project",
            "schema_version": "astra.rag.ingestion-request.v1",
            "chunking_policy_version": "astra.rag.chunking.v1",
        },
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "project_binding_mismatch"
