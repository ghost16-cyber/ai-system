import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import type {
  CanonicalProjectCollection,
  CanonicalProjectResponse,
} from "../src/types/contracts.ts";

import {
  exactPlanApprovalRequest,
  mergeProjectDeliveryAction,
  projectDeliveryActionFromPayload,
} from "../src/state/projectDeliveryState.ts";

function payload(status = "awaiting_plan_approval") {
  return {
    action_type: "project_delivery",
    technical_details: {
      progress: {
        completed_work_units: 0, total_work_units: 2,
        satisfied_required_criteria: 1, total_required_criteria: 2,
      },
      project_delivery: {
        delivery_job_id: "delivery-1", status, original_user_request: "Deliver the greeting feature.",
        specification: {
          normalized_objective: "Deliver the greeting feature", specification_hash: "a".repeat(64),
          specification_source: "deterministic", in_scope_requirements: ["Return a greeting"],
          explicit_exclusions: ["No deployment"], assumptions: [], requested_deliverables: ["Greeting implementation"],
          acceptance_criteria: [
            { criterion_id: "criterion-01", requirement: "Greeting works", required: true, verification_mode: "existing_automated_test", verification_state: "pending" },
            { criterion_id: "criterion-02", requirement: "No unrelated changes", required: true, verification_mode: "exact_diff_or_content_assertion", verification_state: "pending" },
          ],
        },
        plan: {
          plan_hash: "b".repeat(64), plan_revision: 1, plan_source: "deterministic", confidence: .92,
          work_units: [
            { work_unit_id: "wu-01", title: "Implement greeting", objective: "Update app", status: "ready", dependencies: [], expected_files: ["app.py", "../escape.py"], criterion_references: ["criterion-01"] },
            { work_unit_id: "wu-02", title: "Add verification", objective: "Update tests", status: "pending", dependencies: ["wu-01"], expected_files: ["test_app.py"], criterion_references: ["criterion-02"] },
          ],
        },
        plan_approval: null, active_work_unit_id: null,
        clarifications: [], patch_references: [{ patch_id: "patch-1" }],
        command_references: [{ plan_id: "command-1" }],
        verification_records: [{ criterion_id: "criterion-01", state: "satisfied" }],
        scope_changes: [], rollback_records: [], handoff: null,
      },
    },
  };
}

test("Stage 9 task specification, plan, progress, and safe paths normalize", () => {
  const action = projectDeliveryActionFromPayload(payload());
  assert.ok(action);
  assert.equal(action.deliveryJobId, "delivery-1");
  assert.equal(action.specificationSource, "deterministic");
  assert.equal(action.criteria[0].state, "stale");
  assert.equal(action.criteria[0].verifierOutcome, "stale");
  assert.deepEqual(action.plan?.workUnits[0].files, ["app.py"]);
  assert.deepEqual(action.progress, {
    completedWorkUnits: 0, totalWorkUnits: 2,
    satisfiedRequiredCriteria: 0, totalRequiredCriteria: 2,
  });
  assert.deepEqual(action.patchIds, ["patch-1"]);
  assert.deepEqual(action.commandPlanIds, ["command-1"]);
});

test("fresh typed verifier evidence and v2 approval are shown as trusted", () => {
  const value = payload("plan_approved");
  const delivery = value.technical_details.project_delivery as Record<string, unknown>;
  delivery.project_state_hash = "c".repeat(64);
  delivery.project_state_manifest = { complete: true, manifest_hash: "c".repeat(64), incomplete_reasons: [] };
  delivery.plan_revision = { plan_revision_id: "revision-1", content_hash: "b".repeat(64) };
  delivery.plan_approval = {
    plan_revision_id: "revision-1", plan_content_hash: "b".repeat(64),
  };
  delivery.verifier_results = [{
    criterion_id: "criterion-01", outcome: "passed",
    input_manifest_hash: "c".repeat(64), plan_revision_id: "revision-1",
  }];
  const action = projectDeliveryActionFromPayload(value);
  assert.ok(action);
  assert.equal(action.criteria[0].state, "satisfied");
  assert.equal(action.criteria[0].verifierOutcome, "passed");
  assert.equal(action.plan?.approvalFresh, true);
  assert.equal(action.manifest.complete, true);
});

test("exact plan approval uses the displayed immutable hash", () => {
  const action = projectDeliveryActionFromPayload(payload());
  assert.ok(action);
  assert.deepEqual(exactPlanApprovalRequest(action, "conversation-1"), {
    conversation_id: "conversation-1", immutable_hash: "b".repeat(64),
  });
  assert.equal(exactPlanApprovalRequest({ ...action, status: "plan_approved" }, "conversation-1"), null);
});

test("duplicate delivery events replace one card rather than appending state", () => {
  const first = projectDeliveryActionFromPayload(payload());
  const second = projectDeliveryActionFromPayload(payload("plan_approved"));
  assert.ok(first && second);
  assert.equal(mergeProjectDeliveryAction(first, second)?.status, "plan_approved");
});

test("clarification, scope change, Stage 8, rollback, limit, and handoff states survive reload parsing", () => {
  const value = payload("stage8_diagnosis");
  const delivery = value.technical_details.project_delivery as Record<string, unknown>;
  delivery.clarifications = [{ question: "Which component?", answer: null, status: "pending" }];
  delivery.scope_changes = [{ reason_code: "unplanned_file", explanation: "A new file is required.", affected_paths: ["new.py"] }];
  delivery.stage8 = { repair: { status: "offered" } };
  delivery.rollback_records = [{ rollback_id: "rollback-1" }];
  delivery.handoff = {
    completion_status: "awaiting_manual_verification", handoff_hash: "c".repeat(64),
    changed_files: ["app.py"], validation_commands_and_outcomes: ["pytest: passed"],
    known_limitations: ["Browser check pending"], manual_checks_still_required: ["Check UI"], rollback_available: true,
  };
  const action = projectDeliveryActionFromPayload(value);
  assert.ok(action);
  assert.equal(action.clarification?.question, "Which component?");
  assert.equal(action.scopeChanges[0].reason, "unplanned_file");
  assert.ok(action.repair);
  assert.equal(action.rollbackCount, 1);
  assert.equal(action.handoff?.status, "awaiting_manual_verification");
});

test("ordinary and malformed actions remain isolated", () => {
  assert.equal(projectDeliveryActionFromPayload({ action_type: "project_job" }), null);
  assert.equal(projectDeliveryActionFromPayload({ action_type: "project_delivery", technical_details: {} }), null);
});

test("Stage 9 stays in the chat and keeps technical details collapsed", () => {
  const app = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");
  const client = readFileSync(new URL("../src/clients/astraClient.ts", import.meta.url), "utf8");
  assert.match(app, /ProjectDeliveryCard/);
  assert.match(app, /Project delivery progress/);
  assert.match(app, /Approve plan/);
  assert.match(app, /Prepare next patch/);
  assert.match(app, /Verify next criterion/);
  assert.match(app, /Stage 8 diagnosis/);
  assert.match(app, /Scope change detected/);
  assert.match(app, /Client handoff/);
  assert.match(app, /<details className="technical">/);
  assert.doesNotMatch(app, /ProjectDeliveryPage|DeliveryDashboard|ExecutionConsole/);
  assert.match(client, /\/chat\/projects\/deliveries/);
  assert.match(client, /immutable_hash: planHash/);
});

test("Stage 1 canonical read model owns approval, progress, verification, handoff, and action bindings", () => {
  const value = payload("plan_approved");
  const delivery = value.technical_details.project_delivery as Record<string, unknown>;
  delivery.project_control = {
    project_run_id: "delivery-1", lifecycle_state: "awaiting_plan_approval",
    plan_revision_id: "canonical-plan", scope_revision_id: "canonical-scope",
    manifest_hash: "d".repeat(64), manifest_complete: true,
    approval_fresh: false, approval_state: "reapproval_required",
    progress: { completed_work_units: 1, total_work_units: 2 },
    pending_user_action: "approve_plan", state_version: 9,
    handoff_eligible: false,
    verification_summary: { passed: 0, failed: 1, manual_required: 0, total: 1 },
    criterion_states: { "criterion-01": { outcome: "failed", result_hash: "e".repeat(64) } },
  };
  delivery.plan_approval = { plan_revision_id: "canonical-plan", plan_content_hash: "b".repeat(64) };
  const action = projectDeliveryActionFromPayload(value);
  assert.ok(action);
  assert.equal(action.lifecycleState, "awaiting_plan_approval");
  assert.equal(action.plan?.revisionId, "canonical-plan");
  assert.equal(action.scopeRevisionId, "canonical-scope");
  assert.equal(action.stateVersion, 9);
  assert.equal(action.plan?.approvalFresh, false);
  assert.equal(action.criteria[0].state, "failed");
  assert.equal(action.progress.completedWorkUnits, 1);
  assert.equal(action.handoffEligible, false);
  const app = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");
  assert.match(app, /expected_state_version: action\.stateVersion/);
  assert.match(app, /scope_revision_id: action\.scopeRevisionId/);
  assert.doesNotMatch(app, /const handoffReady/);
});

test("canonical queue and coordinator identities survive delivery reload parsing", () => {
  const value = payload("preparing_work_unit");
  const delivery = value.technical_details.project_delivery as Record<string, unknown>;
  delivery.project_control = {
    project_run_id: "delivery-1", lifecycle_state: "work_in_progress",
    plan_revision_id: "plan-1", scope_revision_id: "scope-1",
    manifest_hash: "d".repeat(64), manifest_complete: true,
    approval_fresh: true, approval_state: "approved",
    progress: {}, pending_user_action: "record_patch_result", state_version: 12,
    handoff_eligible: false, verification_summary: {}, criterion_states: {},
    active_execution_attempt_id: "attempt-1",
    active_execution_attempt_type: "patch_application",
    active_execution_attempt_status: "active",
    execution_dispatch_id: "dispatch-1", execution_dispatch_status: "dispatched",
    worker_request_id: "worker-request-1", worker_request_status: "running",
    execution_cancellation_id: "cancellation-1", execution_cancellation_status: "dispatched",
    projection_status: "paused", projection_lag: 2,
    projection_failure_classification: "projection_failed:ValueError",
    execution_evidence_references: { image_digest: "sha256:abc" },
  };
  delivery.coordinator_intents = [{
    coordinator_intent_id: "intent-1", intent_type: "prepare_work_unit", status: "claimed",
  }];
  const action = projectDeliveryActionFromPayload(value);
  assert.ok(action);
  assert.equal(action.execution?.attemptId, "attempt-1");
  assert.equal(action.execution?.workerRequestId, "worker-request-1");
  assert.equal(action.execution?.workerStatus, "running");
  assert.equal(action.execution?.cancellationId, "cancellation-1");
  assert.equal(action.execution?.cancellationStatus, "dispatched");
  assert.equal(action.execution?.projectionStatus, "paused");
  assert.equal(action.execution?.projectionLag, 2);
  assert.equal(action.execution?.recoveryClassification, "projection_failed:ValueError");
  assert.equal(action.coordinatorIntent?.id, "intent-1");
  assert.equal(action.coordinatorIntent?.status, "claimed");
});

test("Stage 3B canonical project API fixtures preserve exact artifact identities", () => {
  const response = {
    schema_version: "astra.project-api.project.v1",
    project: {
      schema_version: "astra.project-control.read-model.v1",
      project_run_id: "project-1", conversation_id: "conversation-1",
      lifecycle_state: "awaiting_plan_approval",
      plan_revision_id: "plan-1", scope_revision_id: "scope-1",
      manifest_hash: "a".repeat(64), manifest_complete: true,
      approval_state: "not_approved", approval_fresh: false,
      current_work_unit: null, progress: { completed_work_units: 0, total_work_units: 1 },
      pending_user_action: "approve_plan", verification_summary: {}, criterion_states: {},
      blocked_reason: null, handoff_eligible: false, state_version: 4, terminal: false,
      artifact_references: { plan: "artifact-plan-1" },
      artifact_hashes: { plan: "b".repeat(64) }, next_permitted_action: "approve_plan",
    },
    artifacts: [{
      schema_version: "astra.project-api.artifact-summary.v1",
      artifact_id: "artifact-plan-1", artifact_type: "plan", revision_number: 1,
      binding_hash: "c".repeat(64), content_hash: "b".repeat(64),
      created_at: "2026-07-20T00:00:00+00:00",
    }],
  } satisfies CanonicalProjectResponse;
  const collection = {
    schema_version: "astra.project-api.collection.v1",
    items: [response], count: 1,
  } satisfies CanonicalProjectCollection;

  assert.equal(collection.items[0].project.artifact_references.plan, "artifact-plan-1");
  assert.equal(collection.items[0].artifacts[0].content_hash, "b".repeat(64));
});
