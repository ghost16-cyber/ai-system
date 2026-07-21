import assert from "node:assert/strict";
import test from "node:test";

import {
  canonicalProjectActionFromResponse,
  exactProjectMutationRequest,
  mergeCanonicalProjectAction,
  shouldRemoveCanonicalProject,
} from "../src/state/projectControlState.ts";

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
