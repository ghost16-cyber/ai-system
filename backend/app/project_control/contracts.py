from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


PROJECT_RUN_VERSION = "astra.project-control.project-run.v1"
SCOPE_REVISION_VERSION = "astra.project-control.scope-revision.v1"
PLAN_REVISION_VERSION = "astra.project-control.plan-revision.v1"
APPROVAL_GRANT_VERSION = "astra.project-control.approval-grant.v1"
EXECUTION_ATTEMPT_VERSION = "astra.project-control.execution-attempt.v1"
PROJECT_EVENT_VERSION = "astra.project-control.event.v1"
PROJECT_COMMAND_VERSION = "astra.project-control.command.v1"
TRANSITION_RESULT_VERSION = "astra.project-control.transition-result.v1"
PROJECT_READ_MODEL_VERSION = "astra.project-control.read-model.v1"


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def content_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProjectLifecycle(StrEnum):
    SPECIFICATION_PENDING = "specification_pending"
    CLARIFICATION_REQUIRED = "clarification_required"
    MANIFEST_REQUIRED = "manifest_required"
    PLANNING = "planning"
    AWAITING_PLAN_APPROVAL = "awaiting_plan_approval"
    READY_FOR_WORK = "ready_for_work"
    WORK_IN_PROGRESS = "work_in_progress"
    AWAITING_PATCH_APPROVAL = "awaiting_patch_approval"
    AWAITING_COMMAND_APPROVAL = "awaiting_command_approval"
    VERIFICATION_PENDING = "verification_pending"
    REPAIR_REQUIRED = "repair_required"
    SCOPE_CHANGE_REQUIRED = "scope_change_required"
    ROLLBACK_PENDING = "rollback_pending"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    HANDOFF_READY = "handoff_ready"
    HANDED_OFF = "handed_off"


TERMINAL_LIFECYCLES = frozenset({ProjectLifecycle.CANCELLED, ProjectLifecycle.COMPLETED})


class ProjectCommandType(StrEnum):
    INITIALIZE_PROJECT = "initialize_project"
    ATTACH_SPECIFICATION = "attach_specification"
    REGISTER_MANIFEST = "register_manifest"
    PROPOSE_PLAN_REVISION = "propose_plan_revision"
    APPROVE_PLAN = "approve_plan"
    BEGIN_WORK_UNIT = "begin_work_unit"
    RECORD_PATCH_PREVIEW = "record_patch_preview"
    APPROVE_PATCH = "approve_patch"
    BEGIN_PATCH_APPLICATION = "begin_patch_application"
    RECORD_PATCH_RESULT = "record_patch_result"
    RECORD_COMMAND_PREVIEW = "record_command_preview"
    APPROVE_COMMAND = "approve_command"
    BEGIN_COMMAND_EXECUTION = "begin_command_execution"
    RECORD_COMMAND_RESULT = "record_command_result"
    REQUEST_VERIFICATION = "request_verification"
    RECORD_VERIFIER_RESULT = "record_verifier_result"
    REQUEST_CLARIFICATION = "request_clarification"
    MARK_BLOCKED = "mark_blocked"
    REVISE_SCOPE = "revise_scope"
    INITIATE_REPAIR = "initiate_repair"
    RECORD_ROLLBACK = "record_rollback"
    COMPLETE_WORK_UNIT = "complete_work_unit"
    REQUEST_HANDOFF = "request_handoff"
    FINALIZE_PROJECT = "finalize_project"
    CANCEL_PROJECT = "cancel_project"
    RECONCILE_LEGACY = "reconcile_legacy"
    RECOVER_ATTEMPT = "recover_attempt"


class ApprovalType(StrEnum):
    PLAN = "plan"
    PATCH = "patch"
    COMMAND = "command"
    MANUAL_VERIFICATION = "manual_verification"
    HANDOFF = "handoff"


class ExecutionAttemptType(StrEnum):
    WORK_UNIT = "work_unit_execution"
    PATCH = "patch_application"
    COMMAND = "command_execution"
    VERIFICATION = "verification"
    REPAIR = "repair"
    ROLLBACK = "rollback"
    HANDOFF = "handoff_generation"


class ExecutionAttemptStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"
    TIMED_OUT = "timed_out"


class ScopeRevision(StrictModel):
    schema_version: str = SCOPE_REVISION_VERSION
    scope_revision_id: str
    project_run_id: str
    specification_hash: str = Field(min_length=64, max_length=64)
    revision_number: int = Field(ge=1)
    included_paths: tuple[str, ...] = ()
    excluded_paths: tuple[str, ...] = ()
    allowed_operations: tuple[str, ...] = ()
    parent_revision_id: str | None = None
    reason: str
    content_hash: str = Field(min_length=64, max_length=64)
    created_at: datetime


class PlanRevision(StrictModel):
    schema_version: str = PLAN_REVISION_VERSION
    plan_revision_id: str
    project_run_id: str
    specification_hash: str = Field(min_length=64, max_length=64)
    scope_revision_id: str
    workspace_id: str
    repository_root: str
    repository_root_fingerprint: str
    required_manifest_hash: str = Field(min_length=64, max_length=64)
    acceptance_criteria: tuple[dict[str, Any], ...] = ()
    work_units: tuple[dict[str, Any], ...] = ()
    configured_limits: dict[str, int] = Field(default_factory=dict)
    revision_number: int = Field(ge=1)
    parent_revision_id: str | None = None
    supersedes_revision_id: str | None = None
    content_hash: str = Field(min_length=64, max_length=64)
    created_at: datetime


class ApprovalGrant(StrictModel):
    schema_version: str = APPROVAL_GRANT_VERSION
    approval_grant_id: str
    project_run_id: str
    approval_type: ApprovalType
    actor_id: str
    conversation_id: str
    workspace_id: str
    repository_root: str
    repository_root_fingerprint: str
    plan_revision_id: str
    scope_revision_id: str
    specification_hash: str = Field(min_length=64, max_length=64)
    manifest_hash: str = Field(min_length=64, max_length=64)
    expected_state_version: int = Field(ge=1)
    authority: dict[str, Any]
    authority_hash: str = Field(min_length=64, max_length=64)
    created_at: datetime
    invalidated_at: datetime | None = None
    invalidation_reason: str | None = None
    superseded_by_id: str | None = None


class ExecutionAttempt(StrictModel):
    schema_version: str = EXECUTION_ATTEMPT_VERSION
    execution_attempt_id: str
    project_run_id: str
    attempt_type: ExecutionAttemptType
    actor_id: str
    conversation_id: str
    workspace_id: str
    repository_root_fingerprint: str
    plan_revision_id: str
    scope_revision_id: str
    manifest_hash: str
    expected_state_version: int = Field(ge=1)
    authority: dict[str, Any]
    attempt_number: int = Field(ge=1)
    idempotency_key: str
    status: ExecutionAttemptStatus
    result_reference: dict[str, Any] | None = None
    failure_classification: str | None = None
    resulting_manifest_hash: str | None = None
    started_at: datetime
    finished_at: datetime | None = None


class ProjectEvent(StrictModel):
    schema_version: str = PROJECT_EVENT_VERSION
    event_id: str
    sequence: int = Field(ge=1)
    project_run_id: str
    event_type: str
    actor_id: str
    conversation_id: str
    workspace_id: str
    previous_state_version: int = Field(ge=0)
    resulting_state_version: int = Field(ge=1)
    plan_revision_id: str | None = None
    scope_revision_id: str | None = None
    request_id: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class ProjectRun(StrictModel):
    schema_version: str = PROJECT_RUN_VERSION
    project_run_id: str
    conversation_id: str
    workspace_id: str
    repository_root: str
    repository_root_fingerprint: str
    actor_id: str
    task_specification_id: str | None = None
    specification_hash: str | None = None
    current_plan_revision_id: str | None = None
    current_scope_revision_id: str | None = None
    current_manifest_hash: str | None = None
    manifest_complete: bool = False
    active_approval_grant_ids: tuple[str, ...] = ()
    execution_attempt_ids: tuple[str, ...] = ()
    work_unit_state: dict[str, dict[str, Any]] = Field(default_factory=dict)
    verification_state: dict[str, dict[str, Any]] = Field(default_factory=dict)
    handoff_eligible: bool = False
    lifecycle_status: ProjectLifecycle
    state_version: int = Field(ge=1)
    blocked_reason: str | None = None
    pending_user_action: str | None = None
    created_at: datetime
    updated_at: datetime
    terminal_at: datetime | None = None
    terminal_reason: str | None = None
    migrated_from: str | None = None
    requires_reapproval: bool = False


class ProjectCommand(StrictModel):
    schema_version: str = PROJECT_COMMAND_VERSION
    command_type: ProjectCommandType
    project_run_id: str
    conversation_id: str
    workspace_id: str
    repository_root: str
    repository_root_fingerprint: str
    actor_id: str
    expected_state_version: int = Field(ge=0)
    idempotency_key: str = Field(min_length=1, max_length=200)
    plan_revision_id: str | None = None
    scope_revision_id: str | None = None
    manifest_hash: str | None = None
    authority_scope: dict[str, Any] = Field(default_factory=dict)
    payload: dict[str, Any] = Field(default_factory=dict)


class TransitionResult(StrictModel):
    schema_version: str = TRANSITION_RESULT_VERSION
    project_run_id: str
    command_type: ProjectCommandType
    idempotency_key: str
    previous_state_version: int
    state_version: int
    lifecycle_status: ProjectLifecycle
    event_id: str
    replayed: bool = False
    created_record_ids: tuple[str, ...] = ()
    read_model: dict[str, Any]


class ProjectReadModel(StrictModel):
    schema_version: str = PROJECT_READ_MODEL_VERSION
    project_run_id: str
    conversation_id: str
    lifecycle_state: ProjectLifecycle
    plan_revision_id: str | None
    scope_revision_id: str | None
    manifest_hash: str | None
    manifest_complete: bool
    approval_state: str
    approval_fresh: bool
    current_work_unit: str | None
    progress: dict[str, int]
    pending_user_action: str | None
    verification_summary: dict[str, int]
    criterion_states: dict[str, dict[str, Any]]
    blocked_reason: str | None
    handoff_eligible: bool
    state_version: int
    terminal: bool
