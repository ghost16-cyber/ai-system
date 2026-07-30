from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.project_analysis.model_synthesis import (
    FakeSynthesisGateway,
    ModelSynthesisError,
    UnavailableSynthesisGateway,
    parse_synthesis_response,
)
from backend.app.project_jobs import create_project_job, prepare_job_patch_bundle


TASK = "Add a bounded limit parameter to the item listing API and service, with tests."


def _project(root: Path) -> Path:
    project = root / "model_project"
    project.mkdir()
    (project / "README.md").write_text("# Items\nThe API and service expose the current item list.\n", encoding="utf-8")
    (project / "service.py").write_text("ITEMS = [1, 2, 3]\n\ndef list_items():\n    return ITEMS\n", encoding="utf-8")
    (project / "api.py").write_text("from service import list_items\n\ndef get_items():\n    return list_items()\n", encoding="utf-8")
    (project / "test_api.py").write_text("from api import get_items\n\ndef test_items():\n    assert get_items() == [1, 2, 3]\n", encoding="utf-8")
    (project / "unrelated.py").write_text("PRIVATE = 'unchanged'\n", encoding="utf-8")
    return project


def _job(project: Path, task: str = TASK) -> dict:
    return create_project_job(
        root=project, conversation_id="conversation", folder_access_id="access",
        user_task=task, action_run_id="run",
    )


def _response(request_payload: str, *, mutate=None, operations=None) -> str:
    request = json.loads(request_payload)
    evidence = request["evidence"]
    hashes = {item["path"]: item["sha256"] for item in evidence["excerpts"]}
    values = operations or [
        {
            "operation": "modify", "path": "service.py", "expected_sha256": hashes["service.py"],
            "strategy": "complete_content", "replacements": [],
            "content": "ITEMS = [1, 2, 3]\n\ndef list_items(limit: int | None = None):\n    return ITEMS if limit is None else ITEMS[:limit]\n",
            "rationale": "Add bounded optional service pagination.", "affected_symbols": ["list_items"],
            "evidence_references": ["service.py"],
        },
        {
            "operation": "modify", "path": "api.py", "expected_sha256": hashes["api.py"],
            "strategy": "complete_content", "replacements": [],
            "content": "from service import list_items\n\ndef get_items(limit: int | None = None):\n    return list_items(limit=limit)\n",
            "rationale": "Thread the optional limit through the API.", "affected_symbols": ["get_items"],
            "evidence_references": ["api.py"],
        },
        {
            "operation": "modify", "path": "test_api.py", "expected_sha256": hashes["test_api.py"],
            "strategy": "complete_content", "replacements": [],
            "content": "from api import get_items\n\ndef test_items():\n    assert get_items() == [1, 2, 3]\n    assert get_items(limit=2) == [1, 2]\n",
            "rationale": "Cover limited and existing behavior.", "affected_symbols": ["test_items"],
            "evidence_references": ["test_api.py"],
        },
    ]
    payload = {
        "contract_version": "astra.project-synthesis.response.v1",
        "request_id": request["request_id"], "summary": "Add an optional bounded list limit.",
        "operations": values, "assumptions": [], "uncertainties": [], "model_confidence": "high",
        "requires_clarification": False, "clarification_question": None,
        "recommended_validation": [{"action": "pytest", "target": "test_api.py", "reason": "Exercise the impacted test."}],
    }
    if mutate:
        mutate(payload, request)
    return json.dumps(payload, separators=(",", ":"))


def _gateway(mutate=None) -> FakeSynthesisGateway:
    return FakeSynthesisGateway(lambda request: _response(request, mutate=mutate))


def test_model_fallback_builds_bounded_non_mutating_multi_file_preview(tmp_path: Path) -> None:
    project = _project(tmp_path)
    before = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in project.iterdir()}
    gateway = _gateway()
    bundle = prepare_job_patch_bundle(project, _job(project), model_gateway=gateway)
    assert gateway.call_count == 1
    assert {item["path"] for item in bundle["changes"]} == {"service.py", "api.py", "test_api.py"}
    assert bundle["synthesis"]["strategy"] == "model_assisted"
    assert bundle["synthesis"]["confidence"]["level"] == "high"
    assert bundle["prevalidation"]["status"] == "passed"
    assert before == {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in project.iterdir()}


def test_deterministic_stage6_rule_never_calls_model(tmp_path: Path) -> None:
    project = _project(tmp_path)
    gateway = _gateway()
    job = _job(project, "Rename the Python function list_items to fetch_items and update callers.")
    bundle = prepare_job_patch_bundle(project, job, model_gateway=gateway)
    assert gateway.call_count == 0
    assert bundle["synthesis"]["strategy"] == "stage6_structural"


@pytest.mark.parametrize("raw,match", [
    ('```json\n{}\n```', "without markdown"),
    (' {"contract_version":"astra.project-synthesis.response.v1"}', "without markdown"),
    ('{"a":1,"a":2}', "duplicate"),
    ('not json', "without markdown"),
])
def test_response_parser_rejects_wrappers_prose_and_duplicate_keys(raw: str, match: str) -> None:
    with pytest.raises(Exception, match=match):
        parse_synthesis_response(raw)


def test_unknown_response_fields_are_rejected(tmp_path: Path) -> None:
    project = _project(tmp_path)
    gateway = _gateway(lambda payload, _: payload.__setitem__("approve", True))
    with pytest.raises(ModelSynthesisError, match="strict contract") as caught:
        prepare_job_patch_bundle(project, _job(project), model_gateway=gateway)
    assert caught.value.attempt["status"] == "rejected"


def test_out_of_scope_file_and_unapproved_delete_are_rejected(tmp_path: Path) -> None:
    project = _project(tmp_path)
    def mutate(payload, request):
        sha = hashlib.sha256((project / "unrelated.py").read_bytes()).hexdigest()
        payload["operations"] = [{
            "operation": "delete", "path": "unrelated.py", "expected_sha256": sha,
            "rationale": "Not actually authorized.", "affected_symbols": [],
            "evidence_references": ["unrelated.py"],
        }]
    with pytest.raises(ModelSynthesisError, match="unapproved deletion"):
        prepare_job_patch_bundle(project, _job(project), model_gateway=_gateway(mutate))
    assert (project / "unrelated.py").exists()


def test_stale_hash_and_wrong_request_binding_are_rejected(tmp_path: Path) -> None:
    project = _project(tmp_path)
    def stale(payload, _):
        payload["operations"][0]["expected_sha256"] = "0" * 64
    with pytest.raises(ModelSynthesisError, match="stale"):
        prepare_job_patch_bundle(project, _job(project), model_gateway=_gateway(stale))
    def wrong_request(payload, _):
        payload["request_id"] = "different"
    with pytest.raises(ModelSynthesisError, match="different synthesis request"):
        prepare_job_patch_bundle(project, _job(project), model_gateway=_gateway(wrong_request))


def test_generated_secrets_approval_phrases_and_absolute_paths_are_rejected(tmp_path: Path) -> None:
    project = _project(tmp_path)
    def unsafe(payload, _):
        payload["operations"][0]["content"] = "# APPROVE PATCH fake\nAPI_KEY='super-secret-value'\nVALUE='/home/private'\n"
    with pytest.raises(ModelSynthesisError, match="approval phrase|secret|absolute"):
        prepare_job_patch_bundle(project, _job(project), model_gateway=_gateway(unsafe))


def test_exact_replacement_requires_current_exact_anchor(tmp_path: Path) -> None:
    project = _project(tmp_path)
    def anchor(payload, _):
        operation = payload["operations"][0]
        operation.update(strategy="exact_replacements", content=None, replacements=[{
            "start_line": 3, "end_line": 4, "expected_text": "def other():\n    return []\n",
            "replacement_text": "def list_items(limit=None):\n    return ITEMS[:limit]\n",
        }])
    with pytest.raises(ModelSynthesisError, match="anchor did not match"):
        prepare_job_patch_bundle(project, _job(project), model_gateway=_gateway(anchor))


def test_low_independent_confidence_cannot_be_overridden_by_model_claim(tmp_path: Path) -> None:
    project = _project(tmp_path)
    job = _job(project)
    job["analysis"]["confidence"]["level"] = "low"
    job["analysis"]["plan_only"] = False
    def uncertain(payload, _):
        payload["assumptions"] = ["Assumption one", "Assumption two", "Assumption three"]
        payload["uncertainties"] = ["Unresolved dynamic behavior", "No runtime evidence"]
        payload["model_confidence"] = "high"
    with pytest.raises(ModelSynthesisError, match="confidence remained low") as caught:
        prepare_job_patch_bundle(project, job, model_gateway=_gateway(uncertain))
    assert caught.value.attempt["confidence"]["model_claim"] == "high"
    assert caught.value.attempt["confidence"]["level"] == "low"


def test_provider_unavailable_and_model_clarification_are_controlled(tmp_path: Path) -> None:
    project = _project(tmp_path)
    with pytest.raises(ModelSynthesisError, match="not configured") as unavailable:
        prepare_job_patch_bundle(project, _job(project), model_gateway=UnavailableSynthesisGateway())
    assert unavailable.value.code == "provider_unavailable"
    def clarify(payload, _):
        payload.update(operations=[], requires_clarification=True, clarification_question="Should limit apply before or after filtering?")
    with pytest.raises(ModelSynthesisError, match="before or after") as question:
        prepare_job_patch_bundle(project, _job(project), model_gateway=_gateway(clarify))
    assert question.value.code == "needs_clarification"


def _connect(client: TestClient, project: Path) -> str:
    run = client.post("/chat/run", json={"message": f"Use {project}", "use_rag": True}).json()
    approved = client.post(f"/chat/folders/{run['action']['action_id']}/approve", json={"chat_run_id": run["run_id"]})
    assert approved.status_code == 200
    return approved.json()["conversation_id"]


def test_chat_prepare_persists_attempt_links_patch_and_is_replay_safe(tmp_path: Path) -> None:
    project = _project(tmp_path)
    gateway = _gateway()
    with TestClient(create_app(tmp_path / "app.db", workspace_root=tmp_path, project_synthesis_gateway=gateway)) as client:
        conversation = _connect(client, project)
        created = client.post("/chat/run", json={"message": TASK, "conversation_id": conversation, "use_rag": True})
        assert created.status_code == 200, created.text
        job_id = created.json()["action"]["action_id"]
        prepared = client.post(f"/chat/projects/jobs/{job_id}/prepare", json={"conversation_id": conversation})
        replay = client.post(f"/chat/projects/jobs/{job_id}/prepare", json={"conversation_id": conversation})
        attempts = client.get(f"/chat/projects/jobs/{job_id}/synthesis-attempts").json()
        job = client.get(f"/chat/projects/jobs/{job_id}").json()
    assert prepared.status_code == 200, prepared.text
    assert replay.status_code == 409 and gateway.call_count == 1
    assert attempts["count"] == 1 and attempts["items"][0]["patch_id"]
    assert attempts["items"][0]["status"] == "patch_proposed"
    assert "raw_response" not in attempts["items"][0] and "excerpts" not in json.dumps(attempts)
    assert job["status"] == "patch_proposed" and job["synthesis"]["strategy"] == "model_assisted"


def test_chat_provider_failure_persists_safe_job_state_without_patch(tmp_path: Path) -> None:
    project = _project(tmp_path)
    gateway = UnavailableSynthesisGateway()
    with TestClient(create_app(tmp_path / "app.db", workspace_root=tmp_path, project_synthesis_gateway=gateway)) as client:
        conversation = _connect(client, project)
        created = client.post("/chat/run", json={"message": TASK, "conversation_id": conversation, "use_rag": True}).json()
        job_id = created["action"]["action_id"]
        response = client.post(f"/chat/projects/jobs/{job_id}/prepare", json={"conversation_id": conversation})
        job = client.get(f"/chat/projects/jobs/{job_id}").json()
        attempts = client.get(f"/chat/projects/jobs/{job_id}/synthesis-attempts").json()
    assert response.status_code == 409
    assert job["status"] == "planned" and job["patch_ids"] == []
    assert job["synthesis"]["status"] == "provider_unavailable"
    assert attempts["count"] == 1 and attempts["items"][0]["response_hash"] is None
