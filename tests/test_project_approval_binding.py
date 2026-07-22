from __future__ import annotations

import sqlite3

import pytest

from backend.app.project_artifacts import (
    ProjectArtifactBinding,
    ProjectArtifactStore,
    ProjectArtifactType,
    build_project_artifact,
)
from backend.app.project_control import ProjectCommandType
from backend.app.project_control.project_service import CanonicalProjectService
from backend.app.project_control.service import ProjectControlPlane
from backend.app.project_api.routes import create_project_router
from fastapi import FastAPI
from fastapi.testclient import TestClient
from tests.test_project_api import _client, _request
from tests.test_project_coordinator_execution import _command, _runtime


def _created(tmp_path):
    _database, client = _client(tmp_path)
    created = client.post("/chat/projects", json=_request(tmp_path)).json()
    project = created["project"]
    action = next(
        item for item in created["next_permitted_actions"] if item["action"] == "approve_plan"
    )
    return client, project, action


def _approval_body(project, action, **overrides):
    body = {
        "schema_version": "astra.project-api.action.v1",
        "conversation_id": project["conversation_id"],
        "workspace_id": project["workspace_id"],
        "actor_id": project["actor_id"],
        "repository_root_fingerprint": project["repository_root_fingerprint"],
        "expected_state_version": action["expected_state_version"],
        "idempotency_key": "approve-plan-binding",
        "plan_revision_id": action["plan_revision_id"],
        "scope_revision_id": action["scope_revision_id"],
        "manifest_hash": action["manifest_hash"],
        "artifact_id": action["artifact_id"],
        "artifact_type": action["artifact_type"],
        "artifact_hash": action["artifact_hash"],
        "artifact_binding_hash": action["artifact_binding_hash"],
        "payload": action["payload"],
    }
    body.update(overrides)
    return body


def _post(client, project, body):
    return client.post(
        f"/chat/projects/{project['project_run_id']}/actions/approve_plan", json=body
    )


def _canonical(tmp_path):
    database = tmp_path / "binding.db"
    artifacts = ProjectArtifactStore(database)
    control = ProjectControlPlane(database, artifact_store=artifacts)
    control.initialize()
    artifacts.initialize()
    service = CanonicalProjectService(control, artifacts)
    project = service.create_project(
        conversation_id="conversation-b",
        workspace_id="workspace-b",
        repository_root=str(tmp_path),
        repository_root_fingerprint="fingerprint-b",
        actor_id="local-user",
        idempotency_key="create-b",
        folder_authority={
            "status": "completed",
            "action_id": "workspace-b",
            "conversation_id": "conversation-b",
            "workspace_id": "workspace-b",
            "repository_root_fingerprint": "fingerprint-b",
        },
        specification={
            "specification_id": "spec-b",
            "specification_hash": "1" * 64,
            "revision": 1,
            "included_paths": ["package.json"],
        },
        manifest={"manifest_hash": "2" * 64, "complete": True, "revision": 1},
        plan={"revision": 1, "acceptance_criteria": [], "work_units": []},
    )
    return control, artifacts, service, project.project_run_id


def test_exact_valid_plan_approval_succeeds_once_and_replays_safely(tmp_path):
    control, artifacts, _service, project_id = _canonical(tmp_path)
    run = control.get_project(project_id)
    command = _command(
        run,
        ProjectCommandType.APPROVE_PLAN,
        "approve-b",
        authority={"operation": "prepare_work_units"},
        artifact=artifacts.get(run.current_artifact_ids["plan"]),
    )
    first = control.execute(command)
    assert first.lifecycle_status.value == "ready_for_work"
    grant = control.list_approvals(project_id)[0]
    current_plan = artifacts.get(run.current_artifact_ids["plan"])
    assert grant.artifact_binding_hash == current_plan.binding_hash

    # An exact replay (same idempotency key + request hash) returns the stored
    # transition without advancing state or granting a second approval.
    replay = control.execute(command)
    assert replay.state_version == first.state_version
    assert replay.lifecycle_status.value == "ready_for_work"
    assert len(control.list_approvals(project_id)) == 1


def test_lost_http_response_exact_retry_replays_after_process_restart(tmp_path):
    database, client = _client(tmp_path)
    created = client.post("/chat/projects", json=_request(tmp_path)).json()
    project = created["project"]
    action = next(item for item in created["next_permitted_actions"] if item["action"] == "approve_plan")
    body = _approval_body(project, action, idempotency_key="lost-response")
    first = _post(client, project, body)
    assert first.status_code == 200
    client.close()  # simulate loss of the successful response and process restart

    artifacts = ProjectArtifactStore(database)
    control = ProjectControlPlane(database, artifact_store=artifacts)
    control.initialize()
    artifacts.initialize()
    service = CanonicalProjectService(control, artifacts)
    app = FastAPI()
    app.include_router(create_project_router(service))
    with TestClient(app) as restarted:
        replay = _post(restarted, project, body)
        assert replay.status_code == 200, replay.text
    assert len([event for event in control.list_events(project["project_run_id"]) if event.request_id == "lost-response"]) == 1
    assert len(control.list_approvals(project["project_run_id"])) == 1


def test_completed_action_key_rejects_changed_state_and_revision_bindings(tmp_path):
    _database, client = _client(tmp_path)
    created = client.post("/chat/projects", json=_request(tmp_path)).json()
    project = created["project"]
    action = next(item for item in created["next_permitted_actions"] if item["action"] == "approve_plan")
    body = _approval_body(project, action, idempotency_key="bound-replay")
    assert _post(client, project, body).status_code == 200
    for changed in (
        {"expected_state_version": body["expected_state_version"] + 1},
        {"plan_revision_id": "changed-plan"},
        {"artifact_hash": "f" * 64},
    ):
        response = _post(client, project, {**body, **changed})
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "idempotency_conflict"


def test_plan_approval_rejects_artifact_bound_to_another_project(tmp_path):
    control, artifacts, service, project_id = _canonical(tmp_path)
    run = control.get_project(project_id)
    foreign_run = service.create_project(
        conversation_id="conversation-foreign",
        workspace_id="workspace-foreign",
        repository_root=str(tmp_path),
        repository_root_fingerprint="fingerprint-foreign",
        actor_id="local-user",
        idempotency_key="create-foreign",
        folder_authority={
            "status": "completed", "action_id": "workspace-foreign",
            "conversation_id": "conversation-foreign",
            "workspace_id": "workspace-foreign",
            "repository_root_fingerprint": "fingerprint-foreign",
        },
        specification={"specification_hash": "4" * 64, "included_paths": []},
        manifest={"manifest_hash": "5" * 64, "complete": True},
        plan={"acceptance_criteria": [], "work_units": []},
    )
    foreign = artifacts.get(foreign_run.current_plan_artifact_id)
    command = _command(
        run,
        ProjectCommandType.APPROVE_PLAN,
        "approve-foreign",
        authority={"operation": "prepare_work_units"},
        artifact=foreign,
    )
    try:
        control.execute(command)
        raised = None
    except Exception as exc:  # noqa: BLE001 - asserting the typed control error
        raised = exc
    assert raised is not None
    assert raised.code.value in {"repository_root_mismatch", "non_current_artifact"}


def test_control_plane_rejects_non_current_plan_artifact_of_correct_type(tmp_path):
    control, artifacts, _service, project_id = _canonical(tmp_path)
    run = control.get_project(project_id)
    # A second, valid PLAN artifact bound to the same project state, but which is
    # not the current plan pointer the run advanced to.
    shadow = artifacts.put(build_project_artifact(
        artifact_type=ProjectArtifactType.PLAN,
        binding=ProjectArtifactBinding(
            project_run_id=project_id,
            plan_revision_id=run.current_plan_revision_id,
            scope_revision_id=run.current_scope_revision_id,
            manifest_hash=run.current_manifest_hash,
        ),
        payload={"work_units": [], "shadow": True},
        revision_number=2,
    ))
    assert shadow.artifact_id != run.current_artifact_ids["plan"]
    command = _command(
        run,
        ProjectCommandType.APPROVE_PLAN,
        "approve-shadow",
        authority={"operation": "prepare_work_units"},
        artifact=shadow,
    )
    try:
        control.execute(command)
        raised = None
    except Exception as exc:  # noqa: BLE001 - asserting the typed control error
        raised = exc
    assert raised is not None
    assert getattr(raised, "code", None) is not None
    assert raised.code.value == "non_current_artifact"


def test_specification_artifact_is_rejected_for_plan_approval(tmp_path):
    client, project, action = _created(tmp_path)
    artifacts = client.get(f"/chat/projects/{project['project_run_id']}/artifacts").json()
    specification = next(item for item in artifacts if item["artifact_type"] == "specification")
    body = _approval_body(
        project,
        action,
        artifact_id=specification["artifact_id"],
        artifact_type="specification",
        artifact_hash=specification["content_hash"],
        artifact_binding_hash=specification["binding_hash"],
    )
    response = _post(client, project, body)
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_command"


def test_non_current_plan_artifact_of_correct_type_is_rejected(tmp_path):
    client, project, action = _created(tmp_path)
    forged = build_project_artifact(
        artifact_type=ProjectArtifactType.PLAN,
        binding=ProjectArtifactBinding(
            project_run_id=project["project_run_id"],
            plan_revision_id=action["plan_revision_id"],
            scope_revision_id=action["scope_revision_id"],
            manifest_hash=action["manifest_hash"],
        ),
        payload={"unauthorized": "shadow-plan"},
        revision_number=99,
    )
    body = _approval_body(
        project,
        action,
        artifact_id=forged.artifact_id,
        artifact_hash=forged.content_hash,
        artifact_binding_hash=forged.binding_hash,
    )
    response = _post(client, project, body)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "non_current_artifact"


def test_mismatched_content_and_binding_hashes_are_rejected(tmp_path):
    client, project, action = _created(tmp_path)
    bad_content = _post(client, project, _approval_body(project, action, artifact_hash="c" * 64))
    assert bad_content.status_code in (409, 422)
    bad_binding = _post(
        client, project, _approval_body(project, action, artifact_binding_hash="d" * 64)
    )
    assert bad_binding.status_code in (409, 422)


def test_command_approval_payload_cannot_forge_manual_verification_bypass(tmp_path):
    """Regression for a P1 authority-bypass audit finding.

    APPROVE_COMMAND must never let client-supplied payload select a different
    approval type. Manual verification has its own dedicated
    SUBMIT_MANUAL_EVIDENCE command bound to an exact criterion; it must not be
    reachable by attaching `approval_type: manual_verification` to an ordinary
    approve_command request, which would previously skip command execution and
    verification entirely and jump straight to completing the work unit.
    """
    control, artifacts, _coordinator, _executor, project_id = _runtime(tmp_path)
    run = control.get_project(project_id)
    control.execute(_command(
        run, ProjectCommandType.BEGIN_WORK_UNIT, "begin-work-unit",
        payload={"work_unit_id": "work-1"}, authority={"work_unit_id": "work-1"},
    ))
    run = control.get_project(project_id)
    command_id = "command-1"
    preview = artifacts.put(build_project_artifact(
        artifact_type=ProjectArtifactType.COMMAND_PREVIEW,
        binding=ProjectArtifactBinding(
            project_run_id=project_id,
            plan_revision_id=run.current_plan_revision_id,
            scope_revision_id=run.current_scope_revision_id,
            manifest_hash=run.current_manifest_hash,
            coordinator_intent_id="test-coordinator-intent-1",
        ),
        payload={"command_id": command_id, "command": "pytest"},
    ))
    control.execute(_command(
        run, ProjectCommandType.RECORD_COMMAND_PREVIEW, "record-command-preview",
        payload={"command_id": command_id}, artifact=preview,
    ))
    run = control.get_project(project_id)

    # The malicious/buggy client payload that previously forged a bypass.
    approved = control.execute(_command(
        run, ProjectCommandType.APPROVE_COMMAND, "approve-command-forged",
        payload={"command_id": command_id, "approval_type": "manual_verification"},
        authority={"command_id": command_id, "operation": "execute_exact_command"},
        artifact=preview,
    ))
    assert approved.lifecycle_status.value == "work_in_progress"
    assert approved.read_model["next_permitted_action"] == "record_command_result"
    assert approved.read_model["next_permitted_action"] != "complete_work_unit"


def test_replay_survives_artifact_supersession_but_new_request_does_not(tmp_path):
    """Regression: exact replay must not evaluate live state.

    An already-completed action's stored replay must be returned for an exact
    resend even if the project has since legitimately moved its current
    artifact pointer (e.g. a scope revision superseded the approved plan). A
    genuinely new request (different idempotency key) against that same now-
    stale artifact must still fail closed.
    """
    control, artifacts, service, project_id = _canonical(tmp_path)
    run = control.get_project(project_id)
    plan_artifact = artifacts.get(run.current_artifact_ids["plan"])

    class _Req:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    def _approve_plan_request(idempotency_key):
        return _Req(
            conversation_id=run.conversation_id, workspace_id=run.workspace_id,
            actor_id=run.actor_id, repository_root_fingerprint=run.repository_root_fingerprint,
            expected_state_version=run.state_version, idempotency_key=idempotency_key,
            plan_revision_id=run.current_plan_revision_id, scope_revision_id=run.current_scope_revision_id,
            manifest_hash=run.current_manifest_hash,
            artifact_id=plan_artifact.artifact_id, artifact_type=plan_artifact.artifact_type.value,
            artifact_hash=plan_artifact.content_hash, artifact_binding_hash=plan_artifact.binding_hash,
            payload={},
        )

    req = _approve_plan_request("approve-plan-supersede-K")
    first = service.execute_action(project_id, action="approve_plan", request=req)
    assert first.lifecycle_state.value == "ready_for_work"

    service.revise_scope(
        project_run_id=project_id,
        specification={"specification_hash": "6" * 64, "included_paths": []},
        idempotency_key="revise-supersede", reason="test",
    )
    service.record_plan(
        project_run_id=project_id,
        plan={"revision": 2, "acceptance_criteria": [], "work_units": []},
        idempotency_key="propose-supersede",
    )
    run2 = control.get_project(project_id)
    assert run2.current_artifact_ids["plan"] != plan_artifact.artifact_id

    # Exact resend of the ORIGINAL already-completed request must replay, not error.
    replay = service.execute_action(project_id, action="approve_plan", request=req)
    assert replay.lifecycle_state.value == "ready_for_work"
    assert len(control.list_approvals(project_id)) == 1

    # A genuinely new request (new idempotency key) against the now-stale
    # artifact must still fail closed.
    fresh_req = _approve_plan_request("genuinely-new-key-supersede")
    try:
        service.execute_action(project_id, action="approve_plan", request=fresh_req)
        raised = None
    except Exception as exc:  # noqa: BLE001
        raised = exc
    assert raised is not None
    assert raised.code.value == "non_current_artifact"


@pytest.mark.parametrize(
    ("tamper", "expected_code"),
    (
        ("missing_event", "corrupted_stored_state"),
        ("malformed_json", "corrupted_stored_state"),
        ("unsupported_schema", "unsupported_stored_state"),
        ("wrong_action", "idempotency_conflict"),
    ),
)
def test_canonical_action_replay_fails_closed_on_unverifiable_persistence(
    tmp_path, tamper, expected_code
):
    control, artifacts, _service, project_id = _canonical(tmp_path)
    run = control.get_project(project_id)
    request = _command(
        run,
        ProjectCommandType.APPROVE_PLAN,
        "tamper-replay",
        authority={"operation": "prepare_work_units"},
        artifact=artifacts.get(run.current_artifact_ids["plan"]),
    )
    first = control.execute(request)
    with sqlite3.connect(control.database_path) as connection:
        if tamper == "missing_event":
            connection.execute("DELETE FROM project_events WHERE event_id = ?", (first.event_id,))
        elif tamper == "malformed_json":
            connection.execute(
                "UPDATE project_action_replays SET replay_json = '{not-json' "
                "WHERE project_run_id = ? AND idempotency_key = ?",
                (project_id, request.idempotency_key),
            )
        elif tamper == "unsupported_schema":
            connection.execute(
                "UPDATE project_action_replays SET result_schema_version = 'future.v99' "
                "WHERE project_run_id = ? AND idempotency_key = ?",
                (project_id, request.idempotency_key),
            )
        else:
            connection.execute(
                "UPDATE project_action_replays SET action_type = 'approve_patch' "
                "WHERE project_run_id = ? AND idempotency_key = ?",
                (project_id, request.idempotency_key),
            )

    restarted = ProjectControlPlane(control.database_path, artifact_store=artifacts)
    restarted.initialize()
    with pytest.raises(Exception) as error:
        restarted.replay_completed(request)
    assert error.value.code.value == expected_code


def test_project_action_replays_is_the_only_live_replay_authority(tmp_path):
    control, artifacts, _service, project_id = _canonical(tmp_path)
    run = control.get_project(project_id)
    request = _command(
        run,
        ProjectCommandType.APPROVE_PLAN,
        "canonical-replay-only",
        authority={"operation": "prepare_work_units"},
        artifact=artifacts.get(run.current_artifact_ids["plan"]),
    )
    first = control.execute(request)
    event_count = len(control.list_events(project_id))
    approval_count = len(control.list_approvals(project_id))
    with sqlite3.connect(control.database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        replay_count = connection.execute(
            "SELECT COUNT(*) FROM project_action_replays WHERE project_run_id = ? "
            "AND idempotency_key = ?",
            (project_id, request.idempotency_key),
        ).fetchone()[0]
        legacy_count = connection.execute(
            "SELECT COUNT(*) FROM project_idempotency_legacy WHERE project_run_id = ? "
            "AND idempotency_key = ?",
            (project_id, request.idempotency_key),
        ).fetchone()[0]
    assert "project_idempotency" not in tables
    assert replay_count == 1
    assert legacy_count == 0

    restarted = ProjectControlPlane(control.database_path, artifact_store=artifacts)
    restarted.initialize()
    replay = restarted.replay_completed(request)
    assert replay is not None and replay.replayed is True
    assert replay.model_dump(exclude={"replayed"}) == first.model_dump(exclude={"replayed"})
    assert len(restarted.list_events(project_id)) == event_count
    assert len(restarted.list_approvals(project_id)) == approval_count
