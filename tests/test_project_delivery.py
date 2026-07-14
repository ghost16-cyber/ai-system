from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.database.repository import AnalysisRepository
from backend.app.main import create_app
from backend.app.project_analysis.model_synthesis import FakeSynthesisGateway
from backend.app.project_delivery import (
    DeliveryLimits,
    ProjectDeliveryError,
    VerificationMode,
    VerificationState,
    activate_next_work_unit,
    approve_plan,
    create_delivery_job,
    generate_handoff,
    immutable_hash,
    link_patch_preview,
    parse_model_specification,
    record_patch_applied,
    record_rollback,
    record_verification,
    stage6_analysis_hash,
)


TASK = "Deliver the project change in README.md by implementing app.py."


def _project(root: Path) -> Path:
    project = root / "delivery_project"
    project.mkdir()
    (project / "README.md").write_text("Feature: greet returns Hello, Ada!\n", encoding="utf-8")
    (project / "app.py").write_text(
        "def greet(name):\n    # ASTRA_TODO: return f\"Hello, {name}!\"\n    raise NotImplementedError\n",
        encoding="utf-8",
    )
    (project / "test_app.py").write_text(
        "from app import greet\n\ndef test_greet():\n    assert greet('Ada') == 'Hello, Ada!'\n",
        encoding="utf-8",
    )
    (project / "pyproject.toml").write_text("[tool.pytest.ini_options]\ntestpaths=['.']\n", encoding="utf-8")
    (project / "unrelated.py").write_text("VALUE = 'protected'\n", encoding="utf-8")
    return project


def _job(project: Path, message: str = TASK, **kwargs) -> dict:
    return create_delivery_job(
        root=project, conversation_id="conversation", folder_access_id="access",
        user_request=message, action_run_id="run", **kwargs,
    )


def _connect(client: TestClient, project: Path) -> str:
    requested = client.post("/chat/run", json={"message": f"Use {project}", "use_rag": True}).json()
    approved = client.post(
        f"/chat/folders/{requested['action']['action_id']}/approve",
        json={"chat_run_id": requested["run_id"]},
    )
    assert approved.status_code == 200
    return requested["conversation_id"]


def test_deterministic_specification_and_plan_do_not_call_model(tmp_path: Path) -> None:
    project = _project(tmp_path)
    gateway = FakeSynthesisGateway(response="not-json")
    job = _job(project, model_gateway=gateway)
    assert gateway.call_count == 0
    assert job["specification"]["specification_source"] == "deterministic"
    assert job["status"] == "awaiting_plan_approval"
    assert len(job["specification"]["acceptance_criteria"]) >= 2
    assert job["plan"]["specification_hash"] == job["specification"]["specification_hash"]


def test_inflected_implementation_request_is_deterministic(tmp_path: Path) -> None:
    project = _project(tmp_path)
    gateway = FakeSynthesisGateway(response="not-json")
    job = _job(
        project,
        "Deliver the README feature by implementing app.py and testing test_app.py.",
        model_gateway=gateway,
    )
    assert gateway.call_count == 0
    assert job["status"] == "awaiting_plan_approval"
    assert job["specification"]["specification_source"] == "deterministic"


@pytest.mark.parametrize("raw", [
    "", " {}", "```json\n{}\n```", "[]", '{"contract_version":"wrong"}',
    '{"contract_version":"astra.project-delivery.model-specification.v1","unknown":1}',
    '{"contract_version":"astra.project-delivery.model-specification.v1","contract_version":"duplicate"}',
])
def test_model_specification_contract_rejects_malformed_or_unknown_data(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_model_specification(raw)


def test_low_confidence_requests_clarification_and_deduplicates(tmp_path: Path) -> None:
    project = _project(tmp_path)
    job = _job(project, "Deliver something better.")
    assert job["status"] == "clarification_required"
    assert len(job["clarifications"]) == 1
    assert job["clarifications"][0]["status"] == "pending"


def test_plan_hash_is_stable_and_approval_is_exact_and_idempotent(tmp_path: Path) -> None:
    project = _project(tmp_path)
    job = _job(project)
    digest = job["plan"]["plan_hash"]
    with pytest.raises(ProjectDeliveryError, match="exact"):
        approve_plan(job, plan_hash="0" * 64)
    approved = approve_plan(job, plan_hash=digest)
    replay = approve_plan(approved, plan_hash=digest)
    assert replay == approved
    assert approved["plan"]["plan_hash"] == digest
    assert approved["plan_approval"]["authority"] == "prepare_work_units_only"


def test_plan_approval_does_not_modify_files_or_run_commands(tmp_path: Path) -> None:
    project = _project(tmp_path)
    before = {path.name: path.read_bytes() for path in project.iterdir() if path.is_file()}
    job = _job(project)
    approved = approve_plan(job, plan_hash=job["plan"]["plan_hash"])
    assert approved["status"] == "plan_approved"
    assert {path.name: path.read_bytes() for path in project.iterdir() if path.is_file()} == before
    assert approved["patch_references"] == [] and approved["command_references"] == []


def test_activation_is_sequential_and_detects_stale_repository(tmp_path: Path) -> None:
    project = _project(tmp_path)
    job = _job(project)
    approved = approve_plan(job, plan_hash=job["plan"]["plan_hash"])
    active = activate_next_work_unit(approved, root=project)
    assert active["active_work_unit_id"] == "wu-01"
    assert active["plan"]["plan_hash"] == approved["plan"]["plan_hash"]
    with pytest.raises(ProjectDeliveryError, match="one work unit"):
        activate_next_work_unit(active, root=project)
    (project / "unrelated.py").write_text("VALUE = 'concurrent'\n", encoding="utf-8")
    with pytest.raises(ProjectDeliveryError) as error:
        activate_next_work_unit(approved, root=project)
    assert error.value.code == "stale_repository"


def test_scope_expansion_requires_replanning(tmp_path: Path) -> None:
    project = _project(tmp_path)
    job = _job(project)
    active = activate_next_work_unit(approve_plan(job, plan_hash=job["plan"]["plan_hash"]), root=project)
    patch = {
        "patch_id": "patch", "proposal_fingerprint": "f", "file_set": ["outside.py"],
        "changes": [{"relative_path": "outside.py", "before_hash": "a", "after_hash": "b"}],
    }
    updated = link_patch_preview(active, patch=patch)
    assert updated["status"] == "replanning_required"
    assert updated["plan_approval"] is None
    assert updated["scope_changes"][0]["reason_code"] == "unplanned_file"


def test_matching_evidence_is_required_for_verification(tmp_path: Path) -> None:
    project = _project(tmp_path)
    job = _job(project)
    criterion = job["specification"]["acceptance_criteria"][0]
    with pytest.raises(ProjectDeliveryError) as mismatch:
        record_verification(
            job, work_unit_id="wu-01", criterion_id=criterion["criterion_id"],
            state=VerificationState.SATISFIED, method=VerificationMode.BUILD,
        )
    assert mismatch.value.code == "evidence_mismatch"
    with pytest.raises(ProjectDeliveryError) as missing:
        record_verification(
            job, work_unit_id="wu-01", criterion_id=criterion["criterion_id"],
            state=VerificationState.SATISFIED, method=VerificationMode(criterion["verification_mode"]),
        )
    assert missing.value.code == "missing_evidence"


def test_command_success_cannot_satisfy_unrelated_structural_criterion(tmp_path: Path) -> None:
    project = _project(tmp_path)
    job = _job(project)
    criterion = job["specification"]["acceptance_criteria"][0]
    assert criterion["verification_mode"] == "structural_code_inspection"
    with pytest.raises(ProjectDeliveryError):
        record_verification(
            job, work_unit_id="wu-01", criterion_id=criterion["criterion_id"],
            state=VerificationState.SATISFIED, method=VerificationMode.APPROVED_COMMAND,
            command_run_references=["run-1"],
        )


def test_handoff_completion_is_deterministic_and_evidence_backed(tmp_path: Path) -> None:
    project = _project(tmp_path)
    job = _job(project)
    for criterion in job["specification"]["acceptance_criteria"]:
        mode = VerificationMode(criterion["verification_mode"])
        job = record_verification(
            job, work_unit_id="wu-01", criterion_id=criterion["criterion_id"],
            state=VerificationState.SATISFIED, method=mode,
            evidence_references=["evidence"], relevant_file_hashes={"app.py": "a" * 64},
            structural_analysis_references=[job["analysis_id"]] if mode == VerificationMode.STRUCTURAL else [],
        )
    completed = generate_handoff(job)
    replay = generate_handoff(completed)
    assert completed["status"] == "delivery_completed"
    assert replay["handoff"] == completed["handoff"]
    assert completed["handoff"]["completion_status"] == "completed"
    assert completed["handoff"]["handoff_hash"] == immutable_hash({key: value for key, value in completed["handoff"].items() if key != "handoff_hash"})


def test_blocked_and_manual_criteria_prevent_full_completion(tmp_path: Path) -> None:
    project = _project(tmp_path)
    job = _job(project)
    criterion = job["specification"]["acceptance_criteria"][0]
    failed = record_verification(
        job, work_unit_id="wu-01", criterion_id=criterion["criterion_id"],
        state=VerificationState.FAILED, method=VerificationMode(criterion["verification_mode"]),
        failure_explanation="Evidence did not match.",
    )
    assert generate_handoff(failed)["handoff"]["completion_status"] == "blocked"


def test_rollback_invalidates_verification_and_preserves_other_references(tmp_path: Path) -> None:
    project = _project(tmp_path)
    job = _job(project)
    job["patch_references"] = [{
        "patch_id": "p1", "work_unit_id": "wu-01", "status": "applied", "file_set": ["app.py"],
    }]
    criterion = job["specification"]["acceptance_criteria"][0]
    verified = record_verification(
        job, work_unit_id="wu-01", criterion_id=criterion["criterion_id"],
        state=VerificationState.SATISFIED, method=VerificationMode(criterion["verification_mode"]),
        evidence_references=["analysis"], structural_analysis_references=[job["analysis_id"]],
    )
    rolled = record_rollback(verified, patch_id="p1", restored_state_hash="b" * 64)
    assert rolled["verification_records"] == []
    assert rolled["patch_references"][0]["status"] == "rolled_back"
    assert rolled["rollback_records"][0]["invalidated_verification_count"] == 1


def test_repository_persists_reloads_and_optimistically_locks_delivery(tmp_path: Path) -> None:
    project = _project(tmp_path)
    repository = AnalysisRepository(tmp_path / "delivery.db")
    repository.initialize()
    job = _job(project)
    repository.store_project_delivery_job(job)
    current = repository.get_project_delivery_job(job["delivery_job_id"])
    updated = approve_plan(current, plan_hash=current["plan"]["plan_hash"])
    stored = repository.transition_project_delivery_job(updated, expected_version=1)
    assert stored and stored["state_version"] == 2
    assert repository.transition_project_delivery_job(updated, expected_version=1) is None
    reloaded = AnalysisRepository(tmp_path / "delivery.db").get_project_delivery_job(job["delivery_job_id"])
    assert reloaded["plan_approval"] == stored["plan_approval"]


def test_duplicate_immutable_records_and_audit_are_bounded(tmp_path: Path) -> None:
    repository = AnalysisRepository(tmp_path / "delivery.db")
    repository.initialize()
    record = {"created_at": "2026-01-01T00:00:00+00:00", "value": "safe"}
    for _ in range(2):
        repository.store_project_delivery_record(
            delivery_job_id="job", record_type="plan", immutable_hash="a" * 64,
            record=record, record_id="record",
        )
    assert len(repository.list_project_delivery_records("job", "plan")) == 1
    repository.store_project_delivery_audit_event({
        "event_id": "event", "delivery_job_id": "job", "conversation_id": "c",
        "operation": "bounded", "status": "ok", "metadata": {"value": "x" * 10_000},
        "created_at": "2026-01-01T00:00:00+00:00",
    })
    raw = sqlite3.connect(tmp_path / "delivery.db").execute("SELECT metadata_json FROM project_delivery_audit_events").fetchone()[0]
    assert len(raw) <= 4_000


def test_central_limits_are_enforced(tmp_path: Path) -> None:
    project = _project(tmp_path)
    limits = DeliveryLimits(max_work_units=1)
    with pytest.raises(ProjectDeliveryError) as error:
        _job(project, limits=limits)
    assert error.value.code == "limit_reached"


def test_stage6_hash_changes_only_with_structural_project_state(tmp_path: Path) -> None:
    project = _project(tmp_path)
    first = _job(project)
    first_hash = stage6_analysis_hash(first["analysis_index"])
    second = _job(project)
    assert stage6_analysis_hash(second["analysis_index"]) == first_hash
    (project / "app.py").write_text("def greet(name): return name\n", encoding="utf-8")
    third = _job(project)
    assert stage6_analysis_hash(third["analysis_index"]) != first_hash


def test_api_chat_plan_patch_verification_handoff_and_reload(tmp_path: Path) -> None:
    project = _project(tmp_path)
    with TestClient(create_app(tmp_path / "app.db", tmp_path)) as client:
        conversation_id = _connect(client, project)
        started = client.post("/chat/run", json={
            "message": TASK, "conversation_id": conversation_id, "use_rag": True,
        })
        assert started.status_code == 200, started.text
        action = started.json()["action"]
        assert action["action_type"] == "project_delivery"
        delivery = action["technical_details"]["project_delivery"]
        delivery_id = delivery["delivery_job_id"]
        before = (project / "app.py").read_text(encoding="utf-8")
        approved = client.post(
            f"/chat/projects/deliveries/{delivery_id}/plan/approve",
            json={"conversation_id": conversation_id, "immutable_hash": delivery["plan"]["plan_hash"]},
        )
        assert approved.status_code == 200, approved.text
        assert (project / "app.py").read_text(encoding="utf-8") == before
        preview = client.post(
            f"/chat/projects/deliveries/{delivery_id}/prepare", json={"conversation_id": conversation_id},
        )
        assert preview.status_code == 200, preview.text
        patch = preview.json()["action"]["technical_details"]["project_patch"]
        refused = client.post(
            f"/chat/projects/patches/{patch['patch_id']}/apply", json={"chat_run_id": preview.json()["run_id"]},
        )
        assert refused.status_code == 409
        exact = client.post(
            f"/chat/projects/patches/{patch['patch_id']}/approve",
            json={"chat_run_id": preview.json()["run_id"], "confirmation": f"APPROVE PATCH {patch['patch_id']}"},
        )
        assert exact.status_code == 200
        applied = client.post(
            f"/chat/projects/patches/{patch['patch_id']}/apply", json={"chat_run_id": preview.json()["run_id"]},
        )
        assert applied.status_code == 200, applied.text
        reloaded = client.get(f"/chat/projects/deliveries/{delivery_id}")
        assert reloaded.status_code == 200
        assert reloaded.json()["status"] == "patch_applied_not_verified"
        assert (project / "unrelated.py").read_text(encoding="utf-8") == "VALUE = 'protected'\n"


def test_api_wrong_conversation_and_duplicate_plan_click_are_safe(tmp_path: Path) -> None:
    project = _project(tmp_path)
    with TestClient(create_app(tmp_path / "app.db", tmp_path)) as client:
        conversation_id = _connect(client, project)
        run = client.post("/chat/projects/deliveries", json={"conversation_id": conversation_id, "user_request": TASK}).json()
        delivery = run["action"]["technical_details"]["project_delivery"]
        path = f"/chat/projects/deliveries/{delivery['delivery_job_id']}/plan/approve"
        body = {"conversation_id": conversation_id, "immutable_hash": delivery["plan"]["plan_hash"]}
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: client.post(path, json=body), range(2)))
        assert sorted(response.status_code for response in responses) == [200, 409]
        mismatch = client.post(path, json={**body, "conversation_id": "other"})
        assert mismatch.status_code == 409


def test_ordinary_chat_does_not_start_delivery(tmp_path: Path) -> None:
    project = _project(tmp_path)
    with TestClient(create_app(tmp_path / "app.db", tmp_path)) as client:
        conversation_id = _connect(client, project)
        response = client.post("/chat/run", json={
            "message": "What does app.py do?", "conversation_id": conversation_id, "use_rag": True,
        })
        assert response.status_code == 200
        assert (response.json().get("action") or {}).get("action_type") != "project_delivery"
