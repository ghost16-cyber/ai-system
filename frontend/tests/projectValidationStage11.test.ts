import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  exactValidationReviewRequest,
  mergeProjectValidationAction,
  projectValidationActionFromPayload,
} from "../src/state/projectValidationState.ts";

function payload(state = "awaiting_human_review") {
  return {
    action_type: "project_validation",
    technical_details: { project_validation: {
      schema_version: "astra.project-validation.v1", campaign_id: "campaign-1",
      conversation_id: "conversation-1", state, state_version: 8,
      scope: { engagement_id: "engagement-1", revision_id: "revision-1", revision_number: 1, scope_hash: "a".repeat(64), objective: "Deliver a verified report.", deliverables: [], exclusions: [] },
      project: { delivery_job_id: "delivery-1", plan_revision: 1, status: "delivery_completed" },
      workspace: { workspace_id: "workspace-1", display_name: "client-project", isolated: false },
      baseline: { snapshot_id: "snapshot-1", file_count: 20, total_bytes: 4000, stale: false, restorable: true },
      active_run_id: "run-1",
      run: {
        run_id: "run-1", run_number: 1, state, state_version: 7,
        budget_usage: { command_executions: 3, modified_files: 2 },
        acceptance_summary: { total: 2, passed: 1, failed: 0, blocked: 0, human_review: 1, items: [
          { criterion_id: "criterion-1", criterion_text: "Tests pass", result: "passed", blocking: false, human_review_required: false, evidence: [{ evidence_id: "ev-1", type: "approved_command", summary: "Tests passed" }] },
          { criterion_id: "criterion-2", criterion_text: "Layout looks correct", result: "requires_human_review", blocking: false, human_review_required: true, evidence: [] },
        ] },
        deliverables: { complete: true, missing_deliverable_ids: [], artifacts: [{ artifact_id: "artifact-1", deliverable_id: "report", client_name: "Findings report", artifact_type: "markdown_report", exists: true, size_bytes: 1024, human_review_required: false }] },
        regression: { blocking: false, unexpected_change_count: 0, regressed_tests: [], summary: "No regression detected." },
        quality: { overall_score: 88, minimum_score: 75, uncertainty: 0.2, blocking_findings: [], automated_decision: "human_review_required", dimensions: [{ name: "Acceptance coverage", score: 75, confidence: 1, explanation: "Evidence-backed." }] },
        findings: [], automated_decision: "human_review_required", result_hash: "b".repeat(64), human_review: null,
      },
    } },
  };
}

test("validation preparation, acceptance, artifacts, regression and quality normalize safely", () => {
  const action = projectValidationActionFromPayload(payload());
  assert.ok(action?.run);
  assert.equal(action.objective, "Deliver a verified report.");
  assert.equal(action.workspace?.name, "client-project");
  assert.equal(action.baseline?.fileCount, 20);
  assert.equal(action.baseline?.restorable, true);
  assert.equal(action.run.criteria[0].result, "passed");
  assert.equal(action.run.criteria[1].humanReviewRequired, true);
  assert.equal(action.run.artifacts[0].name, "Findings report");
  assert.equal(action.run.regression?.blocking, false);
  assert.equal(action.run.quality?.score, 88);
});

test("exact human delivery review binds scope, result hash and optimistic versions", () => {
  const action = projectValidationActionFromPayload(payload());
  assert.ok(action);
  assert.deepEqual(exactValidationReviewRequest(action, "approve_as_delivery_ready", "Reviewed locally"), {
    conversation_id: "conversation-1", expected_state_version: 8, expected_run_version: 7,
    scope_revision_id: "revision-1", scope_hash: "a".repeat(64),
    validation_result_hash: "b".repeat(64), actor_id: "local-user",
    action: "approve_as_delivery_ready", notes: "Reviewed locally",
  });
  assert.equal(exactValidationReviewRequest({ ...action, status: "delivery_ready" }, "approve_as_delivery_ready"), null);
});

test("failed, budget and remediation states stay visible without false readiness", () => {
  for (const state of ["budget_exceeded", "remediation_required", "delivery_rejected"] as const) {
    const action = projectValidationActionFromPayload(payload(state));
    assert.equal(action?.status, state);
  }
});

test("reload and streaming duplicates replace one campaign card", () => {
  const first = projectValidationActionFromPayload(payload("running"));
  const second = projectValidationActionFromPayload(payload("awaiting_human_review"));
  assert.ok(first && second);
  assert.equal(mergeProjectValidationAction(first, second)?.status, "awaiting_human_review");
});

test("Stage 11 implementation remains chat-native and raw details are collapsed", () => {
  const state = readFileSync(new URL("../src/state/projectValidationState.ts", import.meta.url), "utf8");
  const card = readFileSync(new URL("../src/components/ProjectValidationCard.tsx", import.meta.url), "utf8");
  assert.match(state, /exactValidationReviewRequest/);
  assert.match(card, /Pause safely/);
  assert.match(card, /Resume validation/);
  assert.match(card, /Restore baseline/);
  assert.match(card, /Cancel validation/);
  assert.match(state, /validation_result_hash/);
  assert.doesNotMatch(state, /ValidationDashboard|ProjectValidationPage|DeliveryConsole/);
});
