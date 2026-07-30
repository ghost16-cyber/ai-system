"""Reviewed SQL owned by schema migration 9.

Runtime services must never execute this material directly.  It is kept in one
module so the migration checksum and the read-only shape validator describe the
same canonical project schema.
"""

CANONICAL_PROJECT_SCHEMA_V9_SQL = r"""
CREATE TABLE IF NOT EXISTS project_runs (
    project_run_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL, repository_root_fingerprint TEXT NOT NULL,
    lifecycle_status TEXT NOT NULL,
    state_version INTEGER NOT NULL CHECK(state_version >= 1),
    schema_version TEXT NOT NULL, run_json TEXT NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_project_runs_conversation
    ON project_runs(conversation_id, created_at, project_run_id);
CREATE INDEX IF NOT EXISTS idx_project_runs_status
    ON project_runs(lifecycle_status, updated_at);
CREATE INDEX IF NOT EXISTS idx_project_runs_workspace
    ON project_runs(workspace_id, updated_at);

CREATE TABLE IF NOT EXISTS project_scope_revisions (
    scope_revision_id TEXT PRIMARY KEY, project_run_id TEXT NOT NULL,
    revision_number INTEGER NOT NULL CHECK(revision_number >= 1),
    content_hash TEXT NOT NULL, schema_version TEXT NOT NULL,
    revision_json TEXT NOT NULL, created_at TEXT NOT NULL,
    UNIQUE(project_run_id, revision_number), UNIQUE(project_run_id, content_hash),
    FOREIGN KEY(project_run_id) REFERENCES project_runs(project_run_id)
);
CREATE INDEX IF NOT EXISTS idx_project_scope_project
    ON project_scope_revisions(project_run_id, revision_number);

CREATE TABLE IF NOT EXISTS project_plan_revisions_v3 (
    plan_revision_id TEXT PRIMARY KEY, project_run_id TEXT NOT NULL,
    scope_revision_id TEXT NOT NULL,
    revision_number INTEGER NOT NULL CHECK(revision_number >= 1),
    content_hash TEXT NOT NULL, required_manifest_hash TEXT NOT NULL,
    schema_version TEXT NOT NULL, revision_json TEXT NOT NULL,
    created_at TEXT NOT NULL, UNIQUE(project_run_id, revision_number),
    UNIQUE(project_run_id, content_hash),
    FOREIGN KEY(project_run_id) REFERENCES project_runs(project_run_id),
    FOREIGN KEY(scope_revision_id) REFERENCES project_scope_revisions(scope_revision_id)
);
CREATE INDEX IF NOT EXISTS idx_project_plan_project
    ON project_plan_revisions_v3(project_run_id, revision_number);
CREATE INDEX IF NOT EXISTS idx_project_plan_scope
    ON project_plan_revisions_v3(scope_revision_id);

CREATE TABLE IF NOT EXISTS project_approval_grants (
    approval_grant_id TEXT PRIMARY KEY, project_run_id TEXT NOT NULL,
    approval_type TEXT NOT NULL, plan_revision_id TEXT NOT NULL,
    scope_revision_id TEXT NOT NULL, manifest_hash TEXT NOT NULL,
    authority_hash TEXT NOT NULL, schema_version TEXT NOT NULL,
    grant_json TEXT NOT NULL, created_at TEXT NOT NULL,
    UNIQUE(project_run_id, approval_type, authority_hash),
    FOREIGN KEY(project_run_id) REFERENCES project_runs(project_run_id),
    FOREIGN KEY(plan_revision_id) REFERENCES project_plan_revisions_v3(plan_revision_id),
    FOREIGN KEY(scope_revision_id) REFERENCES project_scope_revisions(scope_revision_id)
);
CREATE INDEX IF NOT EXISTS idx_project_approval_binding
    ON project_approval_grants(project_run_id, plan_revision_id, scope_revision_id, manifest_hash);

CREATE TABLE IF NOT EXISTS project_approval_invalidations (
    invalidation_id TEXT PRIMARY KEY, approval_grant_id TEXT NOT NULL UNIQUE,
    project_run_id TEXT NOT NULL, reason TEXT NOT NULL, superseded_by_id TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(approval_grant_id) REFERENCES project_approval_grants(approval_grant_id),
    FOREIGN KEY(project_run_id) REFERENCES project_runs(project_run_id)
);

CREATE TABLE IF NOT EXISTS project_execution_attempts (
    execution_attempt_id TEXT PRIMARY KEY, project_run_id TEXT NOT NULL,
    attempt_type TEXT NOT NULL, status TEXT NOT NULL, idempotency_key TEXT NOT NULL,
    plan_revision_id TEXT NOT NULL, scope_revision_id TEXT NOT NULL,
    attempt_number INTEGER NOT NULL CHECK(attempt_number >= 1),
    schema_version TEXT NOT NULL, attempt_json TEXT NOT NULL,
    started_at TEXT NOT NULL, finished_at TEXT,
    UNIQUE(project_run_id, attempt_type, idempotency_key),
    UNIQUE(project_run_id, attempt_type, attempt_number),
    FOREIGN KEY(project_run_id) REFERENCES project_runs(project_run_id),
    FOREIGN KEY(plan_revision_id) REFERENCES project_plan_revisions_v3(plan_revision_id),
    FOREIGN KEY(scope_revision_id) REFERENCES project_scope_revisions(scope_revision_id)
);
CREATE INDEX IF NOT EXISTS idx_project_attempt_status
    ON project_execution_attempts(project_run_id, status, started_at);

CREATE TABLE IF NOT EXISTS project_execution_dispatches (
    execution_dispatch_id TEXT PRIMARY KEY, project_run_id TEXT NOT NULL,
    execution_attempt_id TEXT NOT NULL UNIQUE, attempt_type TEXT NOT NULL,
    status TEXT NOT NULL,
    expected_project_state_version INTEGER NOT NULL CHECK(expected_project_state_version >= 1),
    priority INTEGER NOT NULL, enqueue_idempotency_key TEXT NOT NULL,
    available_at TEXT NOT NULL, schema_version TEXT NOT NULL,
    dispatch_json TEXT NOT NULL, worker_request_id TEXT, created_at TEXT NOT NULL,
    dispatched_at TEXT, cancelled_at TEXT, failure_classification TEXT,
    UNIQUE(project_run_id, enqueue_idempotency_key),
    FOREIGN KEY(project_run_id) REFERENCES project_runs(project_run_id),
    FOREIGN KEY(execution_attempt_id) REFERENCES project_execution_attempts(execution_attempt_id)
);
CREATE INDEX IF NOT EXISTS idx_project_dispatch_pending
    ON project_execution_dispatches(status, available_at, priority, created_at);
CREATE INDEX IF NOT EXISTS idx_project_dispatch_project
    ON project_execution_dispatches(project_run_id, created_at);

CREATE TABLE IF NOT EXISTS project_events (
    event_id TEXT PRIMARY KEY, project_run_id TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK(sequence >= 1), event_type TEXT NOT NULL,
    request_id TEXT NOT NULL, previous_state_version INTEGER NOT NULL,
    resulting_state_version INTEGER NOT NULL, schema_version TEXT NOT NULL,
    event_json TEXT NOT NULL, created_at TEXT NOT NULL,
    UNIQUE(project_run_id, sequence), UNIQUE(project_run_id, request_id),
    FOREIGN KEY(project_run_id) REFERENCES project_runs(project_run_id)
);
CREATE INDEX IF NOT EXISTS idx_project_events_project
    ON project_events(project_run_id, sequence);
CREATE INDEX IF NOT EXISTS idx_project_events_request ON project_events(request_id);

CREATE TABLE IF NOT EXISTS project_idempotency (
    project_run_id TEXT NOT NULL, idempotency_key TEXT NOT NULL,
    command_type TEXT NOT NULL, request_hash TEXT NOT NULL,
    result_json TEXT NOT NULL, created_at TEXT NOT NULL,
    PRIMARY KEY(project_run_id, idempotency_key),
    FOREIGN KEY(project_run_id) REFERENCES project_runs(project_run_id)
);
CREATE INDEX IF NOT EXISTS idx_project_idempotency_request
    ON project_idempotency(idempotency_key, command_type);

CREATE TABLE IF NOT EXISTS project_legacy_reconciliations (
    legacy_type TEXT NOT NULL, legacy_id TEXT NOT NULL,
    project_run_id TEXT NOT NULL UNIQUE, legacy_hash TEXT NOT NULL,
    canonical_generation TEXT NOT NULL DEFAULT 'legacy', created_at TEXT NOT NULL,
    PRIMARY KEY(legacy_type, legacy_id),
    FOREIGN KEY(project_run_id) REFERENCES project_runs(project_run_id)
);

CREATE TABLE IF NOT EXISTS project_worker_requests (
    worker_request_id TEXT PRIMARY KEY, project_run_id TEXT NOT NULL,
    execution_attempt_id TEXT NOT NULL UNIQUE, attempt_type TEXT NOT NULL,
    status TEXT NOT NULL, priority INTEGER NOT NULL, available_at TEXT NOT NULL,
    delivery_count INTEGER NOT NULL CHECK(delivery_count >= 0),
    max_deliveries INTEGER NOT NULL CHECK(max_deliveries >= 1),
    lease_owner TEXT, lease_token_hash TEXT, lease_expires_at TEXT,
    heartbeat_at TEXT, cancellation_requested_at TEXT, failure_classification TEXT,
    request_hash TEXT NOT NULL, enqueue_idempotency_key TEXT NOT NULL,
    schema_version TEXT NOT NULL, request_json TEXT NOT NULL, result_json TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, finished_at TEXT,
    canonical_reconciled_at TEXT, UNIQUE(project_run_id, enqueue_idempotency_key),
    FOREIGN KEY(project_run_id) REFERENCES project_runs(project_run_id),
    FOREIGN KEY(execution_attempt_id) REFERENCES project_execution_attempts(execution_attempt_id)
);
CREATE INDEX IF NOT EXISTS idx_project_worker_claim
    ON project_worker_requests(status, available_at, priority, created_at);
CREATE INDEX IF NOT EXISTS idx_project_worker_project
    ON project_worker_requests(project_run_id, created_at);
CREATE INDEX IF NOT EXISTS idx_project_worker_lease
    ON project_worker_requests(status, lease_expires_at);

CREATE TABLE IF NOT EXISTS project_worker_events (
    event_id TEXT PRIMARY KEY, worker_request_id TEXT NOT NULL,
    project_run_id TEXT NOT NULL, sequence INTEGER NOT NULL CHECK(sequence >= 1),
    event_type TEXT NOT NULL, worker_id TEXT, schema_version TEXT NOT NULL,
    event_json TEXT NOT NULL, created_at TEXT NOT NULL,
    UNIQUE(worker_request_id, sequence),
    FOREIGN KEY(worker_request_id) REFERENCES project_worker_requests(worker_request_id),
    FOREIGN KEY(project_run_id) REFERENCES project_runs(project_run_id)
);
CREATE INDEX IF NOT EXISTS idx_project_worker_events_request
    ON project_worker_events(worker_request_id, sequence);

CREATE TABLE IF NOT EXISTS project_worker_idempotency (
    project_run_id TEXT NOT NULL, operation_key TEXT NOT NULL,
    operation_type TEXT NOT NULL, request_hash TEXT NOT NULL,
    response_json TEXT NOT NULL, created_at TEXT NOT NULL,
    PRIMARY KEY(project_run_id, operation_key),
    FOREIGN KEY(project_run_id) REFERENCES project_runs(project_run_id)
);
CREATE TABLE IF NOT EXISTS project_worker_runtime_instances (
    worker_id TEXT PRIMARY KEY, execution_backend TEXT NOT NULL,
    status TEXT NOT NULL, schema_version TEXT NOT NULL,
    instance_json TEXT NOT NULL, started_at TEXT NOT NULL,
    last_heartbeat_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_worker_runtime_heartbeat
    ON project_worker_runtime_instances(status, last_heartbeat_at);

CREATE TABLE IF NOT EXISTS project_file_mutation_specs (
    file_mutation_id TEXT PRIMARY KEY, project_run_id TEXT NOT NULL,
    execution_attempt_id TEXT NOT NULL, mutation_kind TEXT NOT NULL,
    authority_id TEXT NOT NULL, spec_hash TEXT NOT NULL UNIQUE,
    schema_version TEXT NOT NULL, spec_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_file_mutation_project
    ON project_file_mutation_specs(project_run_id, created_at);
CREATE TABLE IF NOT EXISTS project_file_mutation_journals (
    file_mutation_id TEXT PRIMARY KEY, status TEXT NOT NULL,
    applied_operations INTEGER NOT NULL DEFAULT 0, journal_json TEXT NOT NULL,
    result_json TEXT, failure_classification TEXT, created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(file_mutation_id) REFERENCES project_file_mutation_specs(file_mutation_id)
);
CREATE INDEX IF NOT EXISTS idx_file_mutation_journal_status
    ON project_file_mutation_journals(status, updated_at);
CREATE TABLE IF NOT EXISTS project_file_mutation_snapshots (
    file_mutation_id TEXT NOT NULL, operation_index INTEGER NOT NULL,
    relative_path TEXT NOT NULL, existed_before INTEGER NOT NULL,
    preimage_sha256 TEXT, snapshot_path TEXT, original_mode INTEGER, staged_path TEXT,
    PRIMARY KEY(file_mutation_id, operation_index),
    FOREIGN KEY(file_mutation_id) REFERENCES project_file_mutation_specs(file_mutation_id)
);
"""

