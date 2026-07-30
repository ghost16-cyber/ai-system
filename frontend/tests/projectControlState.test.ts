import assert from "node:assert/strict";
import test from "node:test";

import {
  canonicalActionRetryIdentity,
  canonicalManualEvidenceRetryIdentity,
  canonicalProjectActionFromResponse,
  clearCanonicalActionRetryIdentity,
  exactProjectMutationRequest,
  mergeCanonicalProjectAction,
  shouldRemoveCanonicalProject,
} from "../src/state/projectControlState.ts";

class MemoryStorage {
  readonly values = new Map<string, string>();
  getItem(key: string) { return this.values.get(key) ?? null; }
  setItem(key: string, value: string) { this.values.set(key, value); }
  removeItem(key: string) { this.values.delete(key); }
}

function response(stateVersion = 4) {
  const artifactHash = "a".repeat(64);
  return {
    schema_version: "astra.project-api.project.v1",
    project: {
      schema_version: "astra.project-control.read-model.v1",
      project_run_id: "project-1", conversation_id: "conversation-1",
      workspace_id: "workspace-1", actor_id: "local-user",
      repository_root_fingerprint: "root-fingerprint",
      lifecycle_state: "awaiting_plan_approval", plan_revision_id: "plan-1",
      scope_revision_id: "scope-1", manifest_hash: "b".repeat(64), manifest_complete: true,
      approval_state: "not_approved", approval_fresh: false, current_work_unit: null,
      progress: { completed_work_units: 0, total_work_units: 1 }, pending_user_action: "approve_plan",
      verification_summary: { passed: 0, total: 1 }, criterion_states: {}, repair_state: {},
      blocked_reason: null, handoff_eligible: false, state_version: stateVersion, terminal: false,
      active_execution_attempt_id: null, active_execution_attempt_type: null, active_execution_attempt_status: null,
      execution_dispatch_id: null, execution_dispatch_status: null, worker_request_id: null, worker_request_status: null,
      execution_failure_classification: null, execution_cancellation_id: null, execution_cancellation_status: null,
      execution_evidence_references: {}, projection_status: "current", projection_lag: 0,
      projection_failure_classification: null, artifact_references: {}, artifact_hashes: {},
      execution_timestamps: {}, next_permitted_action: "approve_plan",
    },
    artifacts: [{
      schema_version: "astra.project-api.artifact-summary.v1", artifact_id: "artifact-plan",
      artifact_type: "plan", revision_number: 1, binding_hash: "c".repeat(64),
      content_hash: artifactHash, created_at: "2026-07-21T00:00:00Z",
    }],
    coordinator: null,
    next_permitted_actions: [{
      schema_version: "astra.project-api.action-descriptor.v1", action: "approve_plan",
      label: "Approve exact plan", requires_approval: true, expected_state_version: stateVersion,
      plan_revision_id: "plan-1", scope_revision_id: "scope-1", manifest_hash: "b".repeat(64),
      artifact_id: "artifact-plan", artifact_hash: artifactHash, payload: {},
      artifact_type: "plan", artifact_binding_hash: "c".repeat(64),
    }],
  };
}

test("canonical response is parsed without browser lifecycle inference", () => {
  const action = canonicalProjectActionFromResponse(response());
  assert.ok(action);
  assert.equal(action.lifecycleState, "awaiting_plan_approval");
  assert.deepEqual(action.nextPermittedActions.map((item) => item.action), ["approve_plan"]);
});

test("exact mutation echoes backend identity, revisions, artifact hash and state", () => {
  const project = canonicalProjectActionFromResponse(response());
  assert.ok(project);
  const request = exactProjectMutationRequest(project, project.nextPermittedActions[0]!, "approval-1");
  assert.deepEqual(request, {
    schema_version: "astra.project-api.action.v1", conversation_id: "conversation-1",
    workspace_id: "workspace-1", actor_id: "local-user", repository_root_fingerprint: "root-fingerprint",
    expected_state_version: 4, idempotency_key: "approval-1", plan_revision_id: "plan-1",
    scope_revision_id: "scope-1", manifest_hash: "b".repeat(64), artifact_id: "artifact-plan",
    artifact_type: "plan", artifact_hash: "a".repeat(64), artifact_binding_hash: "c".repeat(64), payload: {},
  });
});

test("canonical action retries reuse one exact key across attempts and reloads", () => {
  const current = canonicalProjectActionFromResponse(response());
  assert.ok(current);
  const action = current.nextPermittedActions[0]!;
  const storage = new MemoryStorage();
  let created = 0;
  const createKey = () => `approval-${++created}`;

  const first = canonicalActionRetryIdentity(current, action, storage, createKey);
  const retry = canonicalActionRetryIdentity(current, action, storage, createKey);
  const afterReload = canonicalActionRetryIdentity(current, action, storage, createKey);

  assert.ok(first);
  assert.deepEqual(retry, first);
  assert.deepEqual(afterReload, first);
  assert.equal(first.idempotencyKey, "approval-1");
  assert.equal(created, 1);
});

test("changed exact bindings never reuse an uncertain action identity", () => {
  const original = canonicalProjectActionFromResponse(response());
  assert.ok(original);
  const storage = new MemoryStorage();
  let created = 0;
  const first = canonicalActionRetryIdentity(
    original,
    original.nextPermittedActions[0]!,
    storage,
    () => `approval-${++created}`,
  );
  assert.ok(first);

  const changedPayload = response();
  changedPayload.artifacts[0]!.content_hash = "d".repeat(64);
  changedPayload.next_permitted_actions[0]!.artifact_hash = "d".repeat(64);
  const changed = canonicalProjectActionFromResponse(changedPayload);
  assert.ok(changed);
  const second = canonicalActionRetryIdentity(
    changed,
    changed.nextPermittedActions[0]!,
    storage,
    () => `approval-${++created}`,
  );

  assert.ok(second);
  assert.equal(second.storageSlot, first.storageSlot);
  assert.equal(second.idempotencyKey, "approval-2");
  assert.equal(created, 2);
});

test("retry storage is fail-safe and clears only a confirmed exact identity", () => {
  const current = canonicalProjectActionFromResponse(response());
  assert.ok(current);
  const action = current.nextPermittedActions[0]!;
  const storage = new MemoryStorage();
  const identity = canonicalActionRetryIdentity(
    current,
    action,
    storage,
    () => "approval-safe",
  );
  assert.ok(identity?.storageSlot);

  storage.setItem(identity.storageSlot, "{not-json");
  const recovered = canonicalActionRetryIdentity(
    current,
    action,
    storage,
    () => "approval-recovered",
  );
  assert.ok(recovered);
  assert.equal(recovered.idempotencyKey, "approval-recovered");
  clearCanonicalActionRetryIdentity(identity, storage);
  assert.notEqual(storage.getItem(identity.storageSlot), null);
  clearCanonicalActionRetryIdentity(recovered, storage);
  assert.equal(storage.getItem(identity.storageSlot), null);
});

function manualEvidenceRequest(overrides: Record<string, unknown> = {}) {
  return {
    schema_version: "astra.project-api.manual-evidence.v1",
    conversation_id: "conversation-1",
    workspace_id: "workspace-1",
    actor_id: "local-user",
    repository_root_fingerprint: "root-fingerprint",
    expected_state_version: 7,
    idempotency_key: "pending-retry-identity",
    plan_revision_id: "plan-1",
    scope_revision_id: "scope-1",
    manifest_hash: "b".repeat(64),
    work_unit_id: "work-1",
    execution_attempt_id: "attempt-1",
    criterion_id: "criterion-1",
    criterion_hash: "c".repeat(64),
    verification_artifact_id: "verifier-1",
    verification_artifact_hash: "d".repeat(64),
    authority_binding: {
      operation: "submit_manual_evidence",
      project_run_id: "project-1",
      criterion_id: "criterion-1",
      work_unit_id: "work-1",
      execution_attempt_id: "attempt-1",
    },
    decision: "passed",
    evidence_kind: "observation_notes",
    evidence: { notes: "Observed the expected result." },
    ...overrides,
  };
}

test("manual evidence retries reuse one key for the complete exact request", () => {
  const storage = new MemoryStorage();
  let created = 0;
  const createKey = () => `manual-${++created}`;
  const request = manualEvidenceRequest();

  const first = canonicalManualEvidenceRetryIdentity(
    "project-1", "criterion-1", request, storage, createKey,
  );
  const retry = canonicalManualEvidenceRetryIdentity(
    "project-1", "criterion-1", request, storage, createKey,
  );
  const afterReload = canonicalManualEvidenceRetryIdentity(
    "project-1", "criterion-1", { ...request }, storage, createKey,
  );

  assert.ok(first);
  assert.deepEqual(retry, first);
  assert.deepEqual(afterReload, first);
  assert.equal(first.idempotencyKey, "manual-1");
  assert.equal(created, 1);
});

test("changed manual evidence or exact binding gets a fresh retry identity", () => {
  const storage = new MemoryStorage();
  let created = 0;
  const createKey = () => `manual-${++created}`;
  const first = canonicalManualEvidenceRetryIdentity(
    "project-1", "criterion-1", manualEvidenceRequest(), storage, createKey,
  );
  const changedEvidence = canonicalManualEvidenceRetryIdentity(
    "project-1",
    "criterion-1",
    manualEvidenceRequest({ evidence: { notes: "A different observation." } }),
    storage,
    createKey,
  );
  const changedBinding = canonicalManualEvidenceRetryIdentity(
    "project-1",
    "criterion-1",
    manualEvidenceRequest({
      verification_artifact_hash: "e".repeat(64),
    }),
    storage,
    createKey,
  );
  const changedCriterion = canonicalManualEvidenceRetryIdentity(
    "project-1",
    "criterion-2",
    manualEvidenceRequest({ criterion_id: "criterion-2" }),
    storage,
    createKey,
  );

  assert.ok(first);
  assert.ok(changedEvidence);
  assert.ok(changedBinding);
  assert.ok(changedCriterion);
  assert.equal(first.idempotencyKey, "manual-1");
  assert.equal(changedEvidence.idempotencyKey, "manual-2");
  assert.equal(changedBinding.idempotencyKey, "manual-3");
  assert.equal(changedCriterion.idempotencyKey, "manual-4");
  assert.notEqual(changedCriterion.storageSlot, first.storageSlot);
});

test("manual evidence retry identity clears only after exact confirmation", () => {
  const storage = new MemoryStorage();
  const identity = canonicalManualEvidenceRetryIdentity(
    "project-1",
    "criterion-1",
    manualEvidenceRequest(),
    storage,
    () => "manual-confirmed",
  );
  assert.ok(identity?.storageSlot);
  assert.notEqual(storage.getItem(identity.storageSlot), null);

  clearCanonicalActionRetryIdentity(identity, storage);

  assert.equal(storage.getItem(identity.storageSlot), null);
});

test("stale or forged backend action descriptors are not rendered", () => {
  const stale = response();
  stale.next_permitted_actions[0]!.expected_state_version = 3;
  assert.deepEqual(canonicalProjectActionFromResponse(stale)?.nextPermittedActions, []);
  const forged = response();
  forged.next_permitted_actions[0]!.artifact_hash = "d".repeat(64);
  assert.deepEqual(canonicalProjectActionFromResponse(forged)?.nextPermittedActions, []);
});

test("merge keeps the newest canonical state and temporary lookup failures retain cards", () => {
  const current = canonicalProjectActionFromResponse(response(5));
  const stale = canonicalProjectActionFromResponse(response(4));
  assert.ok(current);
  assert.equal(mergeCanonicalProjectAction(current, stale)?.stateVersion, 5);
  assert.equal(shouldRemoveCanonicalProject(503, "project_not_found"), false);
  assert.equal(shouldRemoveCanonicalProject(404, "temporary_failure"), false);
  assert.equal(shouldRemoveCanonicalProject(404, "project_not_found"), true);
});
