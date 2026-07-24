from __future__ import annotations

import json
import hashlib
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
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
    run_deterministic_verifier,
    stage6_analysis_hash,
)
from backend.app.project_workers import FileMutationEngine, ProjectMutationExecutor


TASK = "Deliver the project change in README.md by implementing app.py."
DATASET_DELIVERY_REQUEST = (
    "Analyze household_power_consumption.csv and create a Python script, four PNG charts, "
    "and a Markdown report in this project. Do not modify unrelated files, use external "
    "services, or deploy anything."
)


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


def _bound_delivery_request(
    delivery: dict,
    conversation_id: str,
    idempotency_key: str,
    **extra,
) -> dict:
    return {
        "conversation_id": conversation_id,
        **dict(delivery["compatibility_action_binding"]),
        "idempotency_key": idempotency_key,
        **extra,
    }


def _prepare_with_fresh_binding(
    client: TestClient,
    delivery_id: str,
    conversation_id: str,
    idempotency_key: str,
):
    """Model the reload-and-retry required when a coordinator advances state."""
    for _ in range(100):
        delivery = client.get(f"/chat/projects/deliveries/{delivery_id}").json()
        response = client.post(
            f"/chat/projects/deliveries/{delivery_id}/prepare",
            json=_bound_delivery_request(delivery, conversation_id, idempotency_key),
        )
        if response.status_code != 409 or response.json()["detail"]["code"] != "stale_state_version":
            return response
        time.sleep(0.01)
    raise AssertionError("coordinator kept advancing across exact-bound retries")


def _satisfy(job: dict, project: Path, criterion: dict) -> dict:
    mode = VerificationMode(criterion["verification_mode"])
    result = run_deterministic_verifier(
        job, root=project, criterion_id=criterion["criterion_id"]
    )
    assert result.outcome.value == "passed"
    return record_verification(
        job, work_unit_id=str(result.work_unit_id), criterion_id=criterion["criterion_id"],
        state=VerificationState.SATISFIED, method=mode,
        evidence_references=[result.verifier_result_id],
        relevant_file_hashes={
            "app.py": hashlib.sha256((project / "app.py").read_bytes()).hexdigest()
        },
        structural_analysis_references=[job["analysis_id"]]
        if mode == VerificationMode.STRUCTURAL else [],
        verifier_result=result,
    )


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
    assert missing.value.code == "missing_checker"


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
        job = _satisfy(job, project, criterion)
    completed = generate_handoff(job, root=project)
    replay = generate_handoff(completed, root=project)
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
    assert generate_handoff(failed, root=project)["handoff"]["completion_status"] == "blocked"


def test_rollback_invalidates_verification_and_preserves_other_references(tmp_path: Path) -> None:
    project = _project(tmp_path)
    job = _job(project)
    job["patch_references"] = [{
        "patch_id": "p1", "work_unit_id": "wu-01", "status": "applied", "file_set": ["app.py"],
    }]
    criterion = job["specification"]["acceptance_criteria"][0]
    verified = _satisfy(job, project, criterion)
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
    app = create_app(tmp_path / "app.db", tmp_path)
    with TestClient(app) as client:
        conversation_id = _connect(client, project)
        started = client.post("/chat/run", json={
            "message": f"{TASK} Verify it with pytest.", "conversation_id": conversation_id, "use_rag": True,
        })
        assert started.status_code == 200, started.text
        action = started.json()["action"]
        assert action["action_type"] == "project_delivery"
        delivery = action["technical_details"]["project_delivery"]
        delivery_id = delivery["delivery_job_id"]
        before = (project / "app.py").read_text(encoding="utf-8")
        approved = client.post(
            f"/chat/projects/deliveries/{delivery_id}/plan/approve",
            json=_bound_delivery_request(
                delivery, conversation_id, "approve-delivery-plan",
                immutable_hash=delivery["plan"]["plan_hash"],
            ),
        )
        assert approved.status_code == 200, approved.text
        assert (project / "app.py").read_text(encoding="utf-8") == before
        coordinator_before_prepare = client.get(
            f"/chat/projects/deliveries/{delivery_id}"
        ).json()["coordinator_intents"]
        assert len(coordinator_before_prepare) == 1
        assert coordinator_before_prepare[0]["intent_type"] == "prepare_work_unit"
        coordinator_replay = client.get(
            f"/chat/projects/deliveries/{delivery_id}"
        ).json()["coordinator_intents"]
        assert coordinator_replay[0]["coordinator_intent_id"] == coordinator_before_prepare[0]["coordinator_intent_id"]
        preview = _prepare_with_fresh_binding(
            client, delivery_id, conversation_id, "prepare-delivery-work-unit"
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
        assert exact.status_code == 200, exact.text
        applied = client.post(
            f"/chat/projects/patches/{patch['patch_id']}/apply", json={"chat_run_id": preview.json()["run_id"]},
        )
        assert applied.status_code == 200, applied.text
        assert (project / "app.py").read_text(encoding="utf-8") == before
        reloaded = client.get(f"/chat/projects/deliveries/{delivery_id}")
        assert reloaded.status_code == 200
        canonical = reloaded.json()["canonical_project"]
        assert canonical["active_execution_attempt_id"]
        assert canonical["execution_dispatch_status"] == "pending"
        assert canonical["worker_request_id"] is None

        dispatched = app.state.project_worker_service.dispatch_pending()
        assert len(dispatched.dispatched_request_ids) == 1
        mutation_engine = FileMutationEngine(
            tmp_path / "app.db",
            tmp_path / "data" / "project_mutation_journals",
        )
        mutation_engine.initialize()
        worker = ProjectMutationExecutor(
            app.state.project_worker_service,
            mutation_engine,
        )
        assert worker.run_once("test-mutation-worker") is True

        completed = client.get(f"/chat/projects/deliveries/{delivery_id}")
        assert completed.status_code == 200
        completed_canonical = completed.json()["canonical_project"]
        assert completed_canonical["worker_request_status"] == "succeeded"
        assert completed_canonical["execution_evidence_references"]["file_mutation_id"]
        assert (project / "app.py").read_text(encoding="utf-8") != before
        assert (project / "unrelated.py").read_text(encoding="utf-8") == "VALUE = 'protected'\n"

        subprocess_criterion = next(
            item
            for item in completed.json()["specification"]["acceptance_criteria"]
            if item["verification_mode"] in {
                VerificationMode.EXISTING_TEST.value,
                VerificationMode.NEW_TEST.value,
                VerificationMode.APPROVED_COMMAND.value,
            }
        )
        verification = client.post(
            f"/chat/projects/deliveries/{delivery_id}/verification",
            json=_bound_delivery_request(
                completed.json(), conversation_id, "queue-subprocess-verification",
                criterion_id=subprocess_criterion["criterion_id"],
            ),
        )
        assert verification.status_code == 200, verification.text
        command_run = verification.json()
        command = command_run["action"]["technical_details"]["command_plan"]
        association = {
            "assignment_id": command["assignment_id"],
            "workspace_path": command["workspace"],
            "chat_run_id": command_run["run_id"],
        }
        command_approval = client.post(
            f"/chat/projects/commands/{command['plan_id']}/approve",
            json={
                **association,
                "confirmation": f"APPROVE {command['plan_id']}",
            },
        )
        assert command_approval.status_code == 200, command_approval.text
        execute_body = {
            **association,
            "approval_token": command_approval.json()["approval_token"],
        }
        first_execute = client.post(
            f"/chat/projects/commands/{command['plan_id']}/execute",
            json=execute_body,
        )
        assert first_execute.status_code == 200, first_execute.text
        first_card = first_execute.json()["canonical_project"]
        assert first_execute.json()["status"] == "queued"
        assert first_card["active_execution_attempt_type"] == "command_execution"
        assert first_card["execution_dispatch_status"] == "pending"

        replay_execute = client.post(
            f"/chat/projects/commands/{command['plan_id']}/execute",
            json=execute_body,
        )
        assert replay_execute.status_code == 200, replay_execute.text
        replay_card = replay_execute.json()["canonical_project"]
        assert replay_card["active_execution_attempt_id"] == first_card["active_execution_attempt_id"]
        assert replay_card["execution_dispatch_id"] == first_card["execution_dispatch_id"]

        first_dispatch = app.state.project_worker_service.dispatch_pending()
        second_dispatch = app.state.project_worker_service.dispatch_pending()
        assert len(first_dispatch.dispatched_request_ids) == 1
        assert second_dispatch.dispatched_request_ids == ()
        rehydrated = client.get(f"/chat/projects/deliveries/{delivery_id}").json()["canonical_project"]
        assert rehydrated["active_execution_attempt_id"] == first_card["active_execution_attempt_id"]
        assert rehydrated["execution_dispatch_id"] == first_card["execution_dispatch_id"]
        assert rehydrated["worker_request_id"] == first_dispatch.dispatched_request_ids[0]

        connection = sqlite3.connect(tmp_path / "app.db")
        try:
            command_attempts = connection.execute(
                "SELECT COUNT(*) FROM project_execution_attempts WHERE project_run_id = ? AND attempt_type = 'command_execution'",
                (first_card["project_run_id"],),
            ).fetchone()[0]
            command_requests = connection.execute(
                "SELECT COUNT(*) FROM project_worker_requests WHERE project_run_id = ? AND attempt_type = 'command_execution'",
                (first_card["project_run_id"],),
            ).fetchone()[0]
        finally:
            connection.close()
        assert command_attempts == 1
        assert command_requests == 1


def test_api_canonical_rollback_is_queued_and_rehydrates_exact_identity(tmp_path: Path) -> None:
    project = _project(tmp_path)
    original = (project / "app.py").read_text(encoding="utf-8")
    app = create_app(tmp_path / "app.db", tmp_path)
    with TestClient(app) as client:
        conversation_id = _connect(client, project)
        started = client.post("/chat/run", json={
            "message": TASK, "conversation_id": conversation_id, "use_rag": True,
        }).json()
        delivery = started["action"]["technical_details"]["project_delivery"]
        delivery_id = delivery["delivery_job_id"]
        assert client.post(
            f"/chat/projects/deliveries/{delivery_id}/plan/approve",
            json=_bound_delivery_request(
                delivery, conversation_id, "approve-rollback-plan",
                immutable_hash=delivery["plan"]["plan_hash"],
            ),
        ).status_code == 200
        preview = _prepare_with_fresh_binding(
            client, delivery_id, conversation_id, "prepare-rollback-work-unit"
        ).json()
        patch = preview["action"]["technical_details"]["project_patch"]
        patch_approval = client.post(
            f"/chat/projects/patches/{patch['patch_id']}/approve",
            json={
                "chat_run_id": preview["run_id"],
                "confirmation": f"APPROVE PATCH {patch['patch_id']}",
            },
        )
        assert patch_approval.status_code == 200, patch_approval.text
        assert client.post(
            f"/chat/projects/patches/{patch['patch_id']}/apply",
            json={"chat_run_id": preview["run_id"]},
        ).status_code == 200
        assert len(app.state.project_worker_service.dispatch_pending().dispatched_request_ids) == 1
        patch_worker = ProjectMutationExecutor(
            app.state.project_worker_service,
            app.state.project_mutation_engine,
        )
        assert patch_worker.run_once("patch-worker") is True
        projected = client.get(f"/chat/projects/deliveries/{delivery_id}")
        assert projected.status_code == 200, projected.text
        changed = (project / "app.py").read_text(encoding="utf-8")
        assert changed != original

        rollback = client.post(
            "/chat/projects/rollback/request",
            json={"conversation_id": conversation_id},
        )
        assert rollback.status_code == 200, rollback.text
        rollback_run = rollback.json()
        approved = client.post(
            f"/chat/projects/rollback/{patch['patch_id']}/approve",
            json={
                "chat_run_id": rollback_run["run_id"],
                "confirmation": f"APPROVE ROLLBACK {patch['patch_id']}",
            },
        )
        assert approved.status_code == 200, approved.text
        assert (project / "app.py").read_text(encoding="utf-8") == changed
        queued = client.get(f"/chat/projects/deliveries/{delivery_id}").json()["canonical_project"]
        assert queued["active_execution_attempt_type"] == "rollback"
        assert queued["execution_dispatch_status"] == "pending"

        dispatched = app.state.project_worker_service.dispatch_pending()
        assert len(dispatched.dispatched_request_ids) == 1
        assert patch_worker.run_once("rollback-worker") is True
        rehydrated = client.get(f"/chat/projects/deliveries/{delivery_id}")
        assert rehydrated.status_code == 200, rehydrated.text
        card = rehydrated.json()["canonical_project"]
        assert card["worker_request_id"] == dispatched.dispatched_request_ids[0]
        assert card["worker_request_status"] == "succeeded"
        assert rehydrated.json()["patch_references"][0]["status"] == "rolled_back"
        assert (project / "app.py").read_text(encoding="utf-8") == original


def test_api_wrong_conversation_and_duplicate_plan_click_are_safe(tmp_path: Path) -> None:
    project = _project(tmp_path)
    with TestClient(create_app(tmp_path / "app.db", tmp_path)) as client:
        conversation_id = _connect(client, project)
        run = client.post("/chat/projects/deliveries", json={"conversation_id": conversation_id, "user_request": TASK}).json()
        delivery = run["action"]["technical_details"]["project_delivery"]
        path = f"/chat/projects/deliveries/{delivery['delivery_job_id']}/plan/approve"
        body = _bound_delivery_request(
            delivery, conversation_id, "duplicate-plan-click",
            immutable_hash=delivery["plan"]["plan_hash"],
        )
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


def test_dataset_artifact_request_routes_to_delivery_in_sync_and_streaming_chat(tmp_path: Path) -> None:
    project = _project(tmp_path)
    (project / "household_power_consumption.csv").write_text(
        "Date,Time,Global_active_power\n16/12/2006,17:24:00,4.216\n",
        encoding="utf-8",
    )
    with TestClient(create_app(tmp_path / "app.db", tmp_path)) as client:
        sync_conversation = _connect(client, project)
        sync = client.post("/chat/run", json={
            "message": DATASET_DELIVERY_REQUEST,
            "conversation_id": sync_conversation,
            "use_rag": True,
        })
        stream_conversation = _connect(client, project)
        stream = client.post("/chat/stream", json={
            "message": DATASET_DELIVERY_REQUEST,
            "conversation_id": stream_conversation,
            "use_rag": True,
        })

    assert sync.status_code == 200, sync.text
    assert sync.json()["action"]["action_type"] == "project_delivery"
    sync_delivery = sync.json()["action"]["technical_details"]["project_delivery"]
    assert sync_delivery["status"] == "awaiting_plan_approval"
    assert sync_delivery["clarifications"] == []
    planned_files = [
        path
        for unit in sync_delivery["plan"]["work_units"]
        for path in unit["expected_files"]
    ]
    assert "household_power_consumption_analysis.py" in planned_files
    assert "household_power_consumption_report.md" in planned_files
    assert [path for path in planned_files if path.endswith(".png")] == [
        f"household_power_consumption_chart_{index}.png" for index in range(1, 5)
    ]
    assert "household_power_consumption.csv" not in planned_files
    events = [json.loads(line) for line in stream.text.splitlines() if line.strip()]
    completed = next(event for event in events if event["event"] == "run_completed")
    assert completed["data"]["run"]["action"]["action_type"] == "project_delivery"
    assert any(event["event"] == "project_delivery_updated" for event in events)


def test_dataset_delivery_request_without_conversation_folder_authority_cannot_access_path(tmp_path: Path) -> None:
    project = _project(tmp_path)
    dataset = project / "household_power_consumption.csv"
    dataset.write_text("SECRET_SENTINEL,Value\nprivate,1\n", encoding="utf-8")
    with TestClient(create_app(tmp_path / "app.db", tmp_path)) as client:
        response = client.post("/chat/run", json={
            "message": DATASET_DELIVERY_REQUEST,
            "conversation_id": "conversation-without-folder-authority",
            "use_rag": False,
        })

    assert response.status_code == 200, response.text
    run = response.json()
    assert (run.get("action") or {}).get("action_type") != "project_delivery"
    assert run["source_count"] == 0
    assert "SECRET_SENTINEL" not in response.text


def test_conversation_hydration_returns_current_canonical_delivery_once(tmp_path: Path) -> None:
    project = _project(tmp_path)
    with TestClient(create_app(tmp_path / "app.db", tmp_path)) as client:
        conversation_id = _connect(client, project)
        started = client.post("/chat/run", json={
            "message": TASK, "conversation_id": conversation_id, "use_rag": True,
        }).json()
        delivery = started["action"]["technical_details"]["project_delivery"]
        approved = client.post(
            f"/chat/projects/deliveries/{delivery['delivery_job_id']}/plan/approve",
            json=_bound_delivery_request(
                delivery, conversation_id, "hydrate-plan-approval",
                immutable_hash=delivery["plan"]["plan_hash"],
            ),
        )
        detail = client.get(f"/chat/conversations/{conversation_id}")

    assert approved.status_code == 200, approved.text
    assert detail.status_code == 200, detail.text
    hydrated = detail.json()
    assert hydrated["hydration_version"] == "astra.chat-hydration.v2"
    assert len(hydrated["projects"]) == 1
    assert hydrated["projects"][0]["schema_version"] == "astra.project-api.project.v1"
    assert hydrated["projects"][0]["project"]["project_run_id"] == delivery["delivery_job_id"]
    assert "next_permitted_actions" in hydrated["projects"][0]
    assert [item["delivery_job_id"] for item in hydrated["project_deliveries"]] == [delivery["delivery_job_id"]]
    assert hydrated["project_deliveries"][0]["status"] == "plan_approved"
    assert hydrated["project_deliveries"][0]["plan_approval"]["plan_revision_id"]


def test_stage1_delivery_card_binds_actions_and_reloads_same_control_run(tmp_path: Path) -> None:
    project = _project(tmp_path)
    app = create_app(tmp_path / "app.db", tmp_path)
    with TestClient(app) as client:
        conversation_id = _connect(client, project)
        started = client.post("/chat/projects/deliveries", json={
            "conversation_id": conversation_id, "user_request": TASK,
        })
        assert started.status_code == 200, started.text
        delivery = started.json()["action"]["technical_details"]["project_delivery"]
        control = delivery["project_control"]
        assert control["project_run_id"] == delivery["delivery_job_id"]
        assert control["lifecycle_state"] == "awaiting_plan_approval"
        request = _bound_delivery_request(
            delivery, conversation_id, "browser-plan-approval-1",
            immutable_hash=delivery["plan"]["plan_hash"],
        )
        approved = client.post(
            f"/chat/projects/deliveries/{delivery['delivery_job_id']}/plan/approve", json=request,
        )
        assert approved.status_code == 200, approved.text
        replay = client.post(
            f"/chat/projects/deliveries/{delivery['delivery_job_id']}/plan/approve", json=request,
        )
        assert replay.status_code == 200, replay.text
        conflict = client.post(
            f"/chat/projects/deliveries/{delivery['delivery_job_id']}/plan/approve",
            json={**request, "immutable_hash": "f" * 64},
        )
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["code"] == "idempotency_conflict"
        stale = client.post(
            f"/chat/projects/deliveries/{delivery['delivery_job_id']}/prepare", json={
                **{key: value for key, value in request.items() if key != "immutable_hash"},
                "idempotency_key": "stale-prepare",
            },
        )
        assert stale.status_code == 409
        assert stale.json()["detail"]["code"] == "stale_state_version"
        hydrated = client.get(f"/chat/conversations/{conversation_id}").json()
        restored = hydrated["project_deliveries"][0]["project_control"]
        assert restored["project_run_id"] == control["project_run_id"]
        assert restored["lifecycle_state"] == "ready_for_work"
        assert restored["approval_fresh"] is True
        assert app.state.project_control.get_project(control["project_run_id"]).state_version == restored["state_version"]


def test_pending_request_is_durable_before_stream_and_duplicate_stream_does_not_reexecute(tmp_path: Path) -> None:
    project = _project(tmp_path)
    (project / "household_power_consumption.csv").write_text(
        "Date,Time,Global_active_power\n16/12/2006,17:24:00,4.216\n",
        encoding="utf-8",
    )
    with TestClient(create_app(tmp_path / "app.db", tmp_path)) as client:
        conversation_id = _connect(client, project)
        pending_response = client.post("/chat/requests", json={
            "message": DATASET_DELIVERY_REQUEST,
            "conversation_id": conversation_id,
            "use_rag": True,
        })
        assert pending_response.status_code == 200, pending_response.text
        pending = pending_response.json()

        immediate_reload = client.get(f"/chat/conversations/{conversation_id}")
        assert immediate_reload.status_code == 200, immediate_reload.text
        request_before_stream = next(
            item for item in immediate_reload.json()["requests"]
            if item["request_id"] == pending["request_id"]
        )
        turns_before = len(immediate_reload.json()["turns"])

        stream_body = {
            "message": DATASET_DELIVERY_REQUEST,
            "conversation_id": conversation_id,
            "request_id": pending["request_id"],
            "use_rag": True,
        }
        first_stream = client.post("/chat/stream", json=stream_body)
        first_events = [json.loads(line) for line in first_stream.text.splitlines() if line.strip()]
        first_run = next(event["data"]["run"] for event in first_events if event["event"] == "run_completed")

        second_stream = client.post("/chat/stream", json=stream_body)
        second_events = [json.loads(line) for line in second_stream.text.splitlines() if line.strip()]
        second_run = next(event["data"]["run"] for event in second_events if event["event"] == "run_completed")
        final_reload = client.get(f"/chat/conversations/{conversation_id}").json()

    assert request_before_stream["status"] == "pending"
    assert request_before_stream["execution_attempts"] == 0
    assert first_events[0]["event"] == "request_accepted"
    assert first_events[0]["data"]["request"]["request_id"] == pending["request_id"]
    assert first_run["run_id"] == second_run["run_id"]
    durable = next(item for item in final_reload["requests"] if item["request_id"] == pending["request_id"])
    assert durable["status"] == "completed"
    assert durable["run_id"] == first_run["run_id"]
    assert durable["execution_attempts"] == 1
    assert len(final_reload["turns"]) == turns_before + 1
    assert sum(turn["run_id"] == first_run["run_id"] for turn in final_reload["turns"]) == 1


def test_backend_issues_clean_conversation_identity_and_nonexistent_lookup_is_definitive(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path / "app.db", tmp_path)) as client:
        created = client.post("/chat/conversations", json={})
        missing = client.get("/chat/conversations/permanently-nonexistent")

    assert created.status_code == 200, created.text
    assert created.json()["conversation_id"]
    assert created.json()["turns"] == []
    assert created.json()["requests"] == []
    assert missing.status_code == 404


def test_canonical_and_compatibility_gets_do_not_write_or_reconcile(tmp_path: Path) -> None:
    project = _project(tmp_path)
    database = tmp_path / "app.db"
    with TestClient(create_app(database, tmp_path)) as client:
        conversation_id = _connect(client, project)
        started = client.post("/chat/projects/deliveries", json={
            "conversation_id": conversation_id,
            "user_request": TASK,
        }).json()
        project_id = started["action"]["technical_details"]["project_delivery"]["delivery_job_id"]
        with sqlite3.connect(database) as connection:
            before = tuple(
                connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("project_events", "project_artifacts", "project_delivery_records")
            )
            delivery_json = connection.execute(
                "SELECT job_json FROM project_delivery_jobs WHERE delivery_job_id = ?",
                (project_id,),
            ).fetchone()[0]

        assert client.get(f"/chat/projects/deliveries/{project_id}").status_code == 200
        assert client.get(f"/chat/projects/{project_id}").status_code == 200
        assert client.get(f"/chat/projects/{project_id}/artifacts").status_code == 200
        assert client.get(f"/chat/conversations/{conversation_id}").status_code == 200

        with sqlite3.connect(database) as connection:
            after = tuple(
                connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("project_events", "project_artifacts", "project_delivery_records")
            )
            reloaded_json = connection.execute(
                "SELECT job_json FROM project_delivery_jobs WHERE delivery_job_id = ?",
                (project_id,),
            ).fetchone()[0]
        assert after == before
        assert reloaded_json == delivery_json


def test_active_request_becomes_interrupted_after_backend_restart(tmp_path: Path) -> None:
    database = tmp_path / "app.db"
    repository = AnalysisRepository(database)
    repository.initialize()
    conversation = repository.create_chat_conversation(
        conversation_id="conversation-restart",
        created_at=datetime.now().astimezone(),
    )
    request = repository.create_chat_request(
        request_id="request-restart",
        conversation_id=conversation.conversation_id,
        user_message="Analyze the project",
        request_payload={"message": "Analyze the project", "use_rag": False},
        created_at=datetime.now().astimezone(),
    )
    assert repository.claim_chat_request(request.request_id).status == "active"

    reloaded = AnalysisRepository(database)
    reloaded.initialize()
    reloaded.recover_interrupted_chat_requests()
    interrupted = reloaded.get_chat_request(request.request_id)

    assert interrupted.status == "interrupted"
    assert interrupted.execution_attempts == 1
