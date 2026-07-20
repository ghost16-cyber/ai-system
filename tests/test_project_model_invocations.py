from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3

import pytest
from pydantic import ValidationError

from backend.app.project_control import (
    ProjectCommand,
    ProjectCommandType,
    ProjectControlPlane,
)
from backend.app.project_models import (
    ProjectModelInvocation,
    ProjectModelInvocationError,
    ProjectModelInvocationStatus,
    ProjectModelInvocationStore,
    build_project_model_invocation,
)
from backend.app.project_models.contracts import (
    MAX_MODEL_REQUEST_BYTES,
    MAX_MODEL_RESULT_REFERENCE_BYTES,
    MAX_MODEL_USAGE_BYTES,
)
from backend.app.project_control.contracts import content_hash


def _initialize_project(database: Path) -> None:
    control = ProjectControlPlane(database)
    control.initialize()
    control.execute(
        ProjectCommand(
            command_type=ProjectCommandType.INITIALIZE_PROJECT,
            project_run_id="project-1",
            conversation_id="conversation-1",
            workspace_id="workspace-1",
            repository_root="canonical-root",
            repository_root_fingerprint="root-fingerprint",
            actor_id="local-user",
            expected_state_version=0,
            idempotency_key="initialize",
        )
    )


@pytest.fixture
def store(tmp_path: Path) -> ProjectModelInvocationStore:
    database = tmp_path / "models.db"
    _initialize_project(database)
    value = ProjectModelInvocationStore(database)
    value.initialize()
    return value


def _invocation(**changes):
    values = {
        "project_run_id": "project-1",
        "coordinator_intent_id": "intent-1",
        "purpose": "prepare_patch",
        "evidence_hash": "a" * 64,
        "provider": "test-provider",
        "model_profile": "bounded-code-v1",
        "request_payload": {"artifact_ids": ["specification-1", "manifest-1"]},
    }
    values.update(changes)
    return build_project_model_invocation(**values)


def test_model_invocation_is_durable_and_exactly_idempotent(
    store: ProjectModelInvocationStore,
) -> None:
    invocation = _invocation()
    assert store.create(invocation) == invocation
    assert store.create(invocation) == invocation
    assert store.get(invocation.invocation_id) == invocation
    assert store.list_for_project("project-1") == [invocation]


def test_rebuilt_identical_invocation_returns_original_record(
    store: ProjectModelInvocationStore,
) -> None:
    first = store.create(_invocation())
    rebuilt = _invocation(created_at=first.created_at + timedelta(seconds=5))
    assert store.create(rebuilt) == first


def test_idempotency_key_cannot_bind_a_second_request(
    store: ProjectModelInvocationStore,
) -> None:
    original = store.create(_invocation(idempotency_key="one-model-call"))
    changed = _invocation(
        idempotency_key="one-model-call",
        request_payload={"artifact_ids": ["different"]},
    )
    assert changed.invocation_id == original.invocation_id
    with pytest.raises(ProjectModelInvocationError, match="different request"):
        store.create(changed)


def test_default_invocation_identity_rejects_changed_retry_payload(
    store: ProjectModelInvocationStore,
) -> None:
    original = store.create(_invocation())
    changed = _invocation(request_payload={"artifact_ids": ["changed"]})
    assert changed.invocation_id == original.invocation_id
    with pytest.raises(ProjectModelInvocationError, match="different request"):
        store.create(changed)


@pytest.mark.parametrize(
    "invalid_update",
    [
        {"request_hash": "0" * 64},
        {
            "request_payload": {"value": "x" * (MAX_MODEL_REQUEST_BYTES + 1)},
            "request_hash": content_hash(
                {"value": "x" * (MAX_MODEL_REQUEST_BYTES + 1)}
            ),
        },
        {"provider": ""},
        {
            "status": ProjectModelInvocationStatus.CLAIMED,
            "lease_owner": None,
            "lease_token_hash": None,
            "lease_expires_at": None,
        },
        {"schema_version": "astra.project-models.invocation.v999"},
    ],
    ids=("request-hash", "request-limit", "provider", "lease", "schema"),
)
def test_store_revalidates_constructed_invocation_before_any_insert(
    store: ProjectModelInvocationStore, invalid_update: dict
) -> None:
    values = _invocation().model_dump(mode="python")
    values.update(invalid_update)
    invalid = ProjectModelInvocation.model_construct(**values)

    with pytest.raises((ValidationError, TypeError, ValueError)):
        store.create(invalid)

    with sqlite3.connect(store.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM project_model_invocations").fetchone()[0] == 0


def test_claim_heartbeat_and_success_are_lease_bound(
    store: ProjectModelInvocationStore,
) -> None:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    invocation = store.create(_invocation())
    claimed, token = store.claim(
        invocation.invocation_id, lease_owner="worker-1", lease_seconds=60, now=now
    )
    assert claimed.status == ProjectModelInvocationStatus.CLAIMED
    with pytest.raises(ProjectModelInvocationError, match="lease"):
        store.succeed(
            invocation.invocation_id,
            lease_owner="worker-1",
            lease_token="wrong",
            result_payload={"operations": []},
            now=now + timedelta(seconds=1),
        )
    heartbeat = store.heartbeat(
        invocation.invocation_id,
        lease_owner="worker-1",
        lease_token=token,
        now=now + timedelta(seconds=30),
    )
    assert heartbeat.lease_expires_at == now + timedelta(seconds=90)
    finished = store.succeed(
        invocation.invocation_id,
        lease_owner="worker-1",
        lease_token=token,
        result_payload={"operations": []},
        result_reference={"artifact_id": "patch-1"},
        usage={"input_tokens": 10, "output_tokens": 4},
        now=now + timedelta(seconds=31),
    )
    assert finished.status == ProjectModelInvocationStatus.SUCCEEDED
    assert finished.result_hash
    assert finished.completed_at == now + timedelta(seconds=31)


def test_expired_lease_recovers_to_pending_without_invoking_model(
    store: ProjectModelInvocationStore,
) -> None:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    invocation = store.create(_invocation())
    store.claim(
        invocation.invocation_id, lease_owner="worker-1", lease_seconds=5, now=now
    )
    assert store.recover_expired_leases(now=now + timedelta(seconds=6)) == 1
    recovered = store.get(invocation.invocation_id)
    assert recovered is not None
    assert recovered.status == ProjectModelInvocationStatus.PENDING
    assert recovered.lease_owner is None
    assert store.recover_expired_leases(now=now + timedelta(seconds=7)) == 0


def test_domain_failure_is_persisted_as_terminal_evidence(
    store: ProjectModelInvocationStore,
) -> None:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    invocation = store.create(_invocation())
    _claimed, token = store.claim(
        invocation.invocation_id, lease_owner="worker-1", now=now
    )
    failed = store.fail(
        invocation.invocation_id,
        lease_owner="worker-1",
        lease_token=token,
        failure_classification="provider_rejected_output",
        error_message="The structured response was invalid.",
        now=now + timedelta(seconds=1),
    )
    assert failed.status == ProjectModelInvocationStatus.FAILED
    assert failed.failure_classification == "provider_rejected_output"
    assert failed.completed_at is not None


@pytest.mark.parametrize(
    ("operation", "expected_error"),
    [
        ("result-reference", "result reference"),
        ("usage", "usage"),
        ("error", "error"),
    ],
)
def test_terminal_updates_are_revalidated_before_sqlite_update(
    store: ProjectModelInvocationStore,
    operation: str,
    expected_error: str,
) -> None:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    invocation = store.create(_invocation())
    _claimed, token = store.claim(
        invocation.invocation_id, lease_owner="worker-1", now=now
    )
    before = _invocation_row(store, invocation.invocation_id)

    with pytest.raises(ValidationError, match=expected_error):
        if operation == "result-reference":
            store.succeed(
                invocation.invocation_id,
                lease_owner="worker-1",
                lease_token=token,
                result_payload={},
                result_reference={
                    "value": "x" * (MAX_MODEL_RESULT_REFERENCE_BYTES + 1)
                },
                now=now + timedelta(seconds=1),
            )
        elif operation == "usage":
            store.succeed(
                invocation.invocation_id,
                lease_owner="worker-1",
                lease_token=token,
                result_payload={},
                usage={"value": "x" * (MAX_MODEL_USAGE_BYTES + 1)},
                now=now + timedelta(seconds=1),
            )
        else:
            store.fail(
                invocation.invocation_id,
                lease_owner="worker-1",
                lease_token=token,
                failure_classification="provider_error",
                error_message="é" * 3000,
                now=now + timedelta(seconds=1),
            )

    assert _invocation_row(store, invocation.invocation_id) == before


def test_normalized_invocation_tampering_fails_closed(
    store: ProjectModelInvocationStore,
) -> None:
    invocation = store.create(_invocation())
    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "UPDATE project_model_invocations SET request_hash = ? WHERE invocation_id = ?",
            ("0" * 64, invocation.invocation_id),
        )
    with pytest.raises(ProjectModelInvocationError, match="normalized fields"):
        store.get(invocation.invocation_id)


def _invocation_row(
    store: ProjectModelInvocationStore, invocation_id: str
) -> tuple[object, ...]:
    with sqlite3.connect(store.database_path) as connection:
        row = connection.execute(
            "SELECT status, lease_owner, lease_token_hash, lease_expires_at, invocation_json, updated_at, completed_at "
            "FROM project_model_invocations WHERE invocation_id = ?",
            (invocation_id,),
        ).fetchone()
    assert row is not None
    return tuple(row)
