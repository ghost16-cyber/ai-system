from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from backend.app.project_control.contracts import ProjectLifecycle
from backend.app.project_coordinator import (
    CoordinatorIntentError,
    CoordinatorIntentStatus,
    CoordinatorIntentType,
    ProjectCoordinatorService,
)


class FakeControl:
    def __init__(self, *, max_work_units: int = 2) -> None:
        self.run = SimpleNamespace(
            project_run_id="project-1",
            lifecycle_status=ProjectLifecycle.READY_FOR_WORK,
            pending_user_action="begin_work_unit",
            current_plan_revision_id="plan-1",
            current_scope_revision_id="scope-1",
            current_manifest_hash="a" * 64,
            state_version=7,
        )
        self.events = [SimpleNamespace(event_id="event-1", sequence=1)]
        self.plan = SimpleNamespace(configured_limits={"max_work_units": max_work_units})

    def get_project(self, project_run_id: str):
        assert project_run_id == self.run.project_run_id
        return self.run

    def list_events(self, project_run_id: str):
        assert project_run_id == self.run.project_run_id
        return list(self.events)

    def get_plan_revision(self, plan_revision_id: str):
        assert plan_revision_id == "plan-1"
        return self.plan


def test_reconcile_is_idempotent_and_coordinator_never_changes_lifecycle(tmp_path) -> None:
    control = FakeControl()
    service = ProjectCoordinatorService(tmp_path / "control.db", control)
    service.initialize()

    first = service.reconcile("project-1")
    replay = service.reconcile("project-1")

    assert first is not None and replay is not None
    assert replay.coordinator_intent_id == first.coordinator_intent_id
    assert first.intent_type == CoordinatorIntentType.PREPARE_WORK_UNIT
    assert control.run.lifecycle_status == ProjectLifecycle.READY_FOR_WORK
    assert len(service.list_for_project("project-1")) == 1


def test_claim_expiry_recovery_and_exact_completion_are_durable(tmp_path) -> None:
    control = FakeControl()
    service = ProjectCoordinatorService(tmp_path / "control.db", control)
    service.initialize()
    intent = service.reconcile("project-1")
    assert intent is not None

    claimed = service.claim_next("coordinator-worker", lease_seconds=5)
    assert claimed is not None
    assert claimed.status == CoordinatorIntentStatus.CLAIMED
    assert service.claim_next("other-worker") is None
    recovered = service.recover_expired_leases(
        now=datetime.now(timezone.utc) + timedelta(seconds=10)
    )
    assert recovered == 1
    reclaimed = service.claim_next("coordinator-worker", lease_seconds=30)
    assert reclaimed is not None
    assert reclaimed.coordinator_intent_id == claimed.coordinator_intent_id
    assert reclaimed.lease_token != claimed.lease_token

    with pytest.raises(CoordinatorIntentError):
        service.complete(
            reclaimed.coordinator_intent_id,
            worker_id="coordinator-worker",
            lease_token=str(claimed.lease_token),
            result_reference={"patch_id": "patch-1"},
        )
    completed = service.complete(
        reclaimed.coordinator_intent_id,
        worker_id="coordinator-worker",
        lease_token=str(reclaimed.lease_token),
        result_reference={"patch_id": "patch-1"},
    )
    replay = service.complete(
        reclaimed.coordinator_intent_id,
        worker_id="coordinator-worker",
        lease_token=str(reclaimed.lease_token),
        result_reference={"patch_id": "patch-1"},
    )
    assert completed.status == CoordinatorIntentStatus.COMPLETED
    assert replay.coordinator_intent_id == completed.coordinator_intent_id


def test_durable_intent_budget_rejects_a_second_trigger(tmp_path) -> None:
    control = FakeControl(max_work_units=1)
    service = ProjectCoordinatorService(tmp_path / "control.db", control)
    service.initialize()
    assert service.reconcile("project-1") is not None
    control.events.append(SimpleNamespace(event_id="event-2", sequence=2))
    control.run.state_version += 1

    with pytest.raises(CoordinatorIntentError, match="budget"):
        service.reconcile("project-1")


def test_terminal_and_user_approval_states_do_not_create_background_authority(tmp_path) -> None:
    control = FakeControl()
    service = ProjectCoordinatorService(tmp_path / "control.db", control)
    service.initialize()
    control.run.pending_user_action = "approve_patch:patch-1"
    assert service.reconcile("project-1") is None
    control.run.lifecycle_status = ProjectLifecycle.CANCELLED
    control.run.pending_user_action = "begin_work_unit"
    assert service.reconcile("project-1") is None
