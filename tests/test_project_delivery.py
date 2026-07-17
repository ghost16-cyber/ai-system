from __future__ import annotations

import json
import hashlib
import sqlite3
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
            json={"conversation_id": conversation_id, "immutable_hash": delivery["plan"]["plan_hash"]},
        )
        detail = client.get(f"/chat/conversations/{conversation_id}")

    assert approved.status_code == 200, approved.text
    assert detail.status_code == 200, detail.text
    hydrated = detail.json()
    assert hydrated["hydration_version"] == "astra.chat-hydration.v1"
    assert [item["delivery_job_id"] for item in hydrated["project_deliveries"]] == [delivery["delivery_job_id"]]
    assert hydrated["project_deliveries"][0]["status"] == "plan_approved"
    assert hydrated["project_deliveries"][0]["plan_approval"]["plan_revision_id"]


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
    interrupted = reloaded.get_chat_request(request.request_id)

    assert interrupted.status == "interrupted"
    assert interrupted.execution_attempts == 1
