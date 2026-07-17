import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

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
