import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  canonicalProjectActionFromResponse,
  isCancelledCanonicalProject,
  isCompletedCanonicalProject,
  shouldPollCanonicalProject,
  sortCanonicalProjectEvents,
  type CanonicalProjectAction,
} from "../src/state/projectControlState.ts";
import type { CanonicalProjectEventSummary } from "../src/types/contracts.ts";

function response(overrides: Record<string, unknown> = {}) {
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
      blocked_reason: null, handoff_eligible: false, state_version: 4, terminal: false,
      active_execution_attempt_id: null, active_execution_attempt_type: null, active_execution_attempt_status: null,
      execution_dispatch_id: null, execution_dispatch_status: null, worker_request_id: null, worker_request_status: null,
      execution_failure_classification: null, execution_cancellation_id: null, execution_cancellation_status: null,
      execution_evidence_references: {}, projection_status: "current", projection_lag: 0,
      projection_failure_classification: null, artifact_references: {}, artifact_hashes: {},
      execution_timestamps: {}, next_permitted_action: "approve_plan",
      ...overrides,
    },
    artifacts: [],
    coordinator: null,
    next_permitted_actions: [],
  };
}

function project(overrides: Record<string, unknown> = {}): CanonicalProjectAction {
  const action = canonicalProjectActionFromResponse(response(overrides));
  assert.ok(action);
  return action;
}

test("shouldPollCanonicalProject stops for a terminal project regardless of pending action", () => {
  assert.equal(shouldPollCanonicalProject(project({ terminal: true, lifecycle_state: "completed", pending_user_action: null })), false);
  assert.equal(shouldPollCanonicalProject(project({ terminal: true, lifecycle_state: "cancelled", pending_user_action: null })), false);
});

test("shouldPollCanonicalProject stops while waiting on every human-gated pending action", () => {
  for (const pending of [
    "approve_plan", "approve_patch:abc", "approve_command:abc", "approve_rollback:abc",
    "review_failed_repair", "review_manual_failure:crit", "review_block", "review_interrupted_attempt",
    "submit_manual_evidence:crit", "answer_clarification", "finalize_project",
  ]) {
    assert.equal(
      shouldPollCanonicalProject(project({ pending_user_action: pending })),
      false,
      `expected no polling while pending=${pending}`,
    );
  }
});

test("shouldPollCanonicalProject continues while the coordinator may still be progressing automatically", () => {
  for (const pending of ["begin_work_unit", "request_verification", "initiate_repair", "request_handoff"]) {
    assert.equal(
      shouldPollCanonicalProject(project({ pending_user_action: pending })),
      true,
      `expected polling while pending=${pending}`,
    );
  }
  assert.equal(shouldPollCanonicalProject(project({ pending_user_action: null })), true);
});

test("isCompletedCanonicalProject and isCancelledCanonicalProject are mutually exclusive and match only their own terminal state", () => {
  const completed = project({ terminal: true, lifecycle_state: "completed" });
  const cancelled = project({ terminal: true, lifecycle_state: "cancelled" });
  const blocked = project({ terminal: false, lifecycle_state: "blocked", blocked_reason: "Review required." });
  const failedAttempt = project({ execution_failure_classification: "process_exit_nonzero" });

  assert.equal(isCompletedCanonicalProject(completed), true);
  assert.equal(isCancelledCanonicalProject(completed), false);

  assert.equal(isCancelledCanonicalProject(cancelled), true);
  assert.equal(isCompletedCanonicalProject(cancelled), false);

  assert.equal(isCompletedCanonicalProject(blocked), false);
  assert.equal(isCancelledCanonicalProject(blocked), false);
  assert.equal(blocked.blockedReason, "Review required.");

  assert.equal(isCompletedCanonicalProject(failedAttempt), false);
  assert.equal(isCancelledCanonicalProject(failedAttempt), false);
  assert.equal(failedAttempt.execution.failureClassification, "process_exit_nonzero");
});

function event(sequence: number, label = `event-${sequence}`): CanonicalProjectEventSummary {
  return {
    schema_version: "astra.project-api.event-summary.v1",
    sequence, event_type: "approve_plan", label,
    occurred_at: `2026-01-01T00:00:${String(sequence).padStart(2, "0")}Z`,
  };
}

test("sortCanonicalProjectEvents orders by sequence regardless of arrival order", () => {
  const arrivalOrder = [event(3), event(1), event(2)];
  assert.deepEqual(sortCanonicalProjectEvents(arrivalOrder).map((item) => item.sequence), [1, 2, 3]);
});

test("sortCanonicalProjectEvents does not mutate its input", () => {
  const input = [event(2), event(1)];
  const copy = [...input];
  sortCanonicalProjectEvents(input);
  assert.deepEqual(input, copy);
});

test("App.tsx recognizes canonical_project via an explicit action_type discriminator, not shape sniffing alone", () => {
  const app = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");
  assert.match(app, /run\.action\?\.action_type === "canonical_project"/);
  assert.match(app, /canonicalProjectActionFromResponse\(run\.action\)/);
});

test("App.tsx polls the active canonical project without overlapping requests and stops while terminal or human-gated", () => {
  const app = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");
  assert.match(app, /shouldPollCanonicalProject/);
  assert.match(app, /canonicalPollSignal/);
  assert.match(app, /inFlight/);
  assert.match(app, /window\.clearInterval\(interval\)/);
});

test("App.tsx refreshes the canonical project immediately after performing an action or submitting manual evidence", () => {
  const app = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");
  const refreshCalls = app.match(/refreshCanonicalProject\(/g) ?? [];
  // One definition site plus at least three call sites: the poll tick, the
  // action-performing handler, and the manual-evidence handler.
  assert.ok(refreshCalls.length >= 4, `expected refreshCanonicalProject to be called at least 3 times, source has ${refreshCalls.length} occurrences`);
});

test("ProjectControlCard renders the timeline in sorted order and distinguishes cancelled from completed", () => {
  const source = readFileSync(new URL("../src/components/ProjectControlCard.tsx", import.meta.url), "utf8");
  assert.match(source, /sortCanonicalProjectEvents\(events\)/);
  assert.match(source, /isCompletedCanonicalProject\(project\)/);
  assert.match(source, /isCancelledCanonicalProject\(project\)/);
  assert.match(source, /result cancelled/);
  assert.match(source, /result completed/);
});

test("the chat client exposes canonical project events against the events endpoint", () => {
  const client = readFileSync(new URL("../src/clients/astraClient.ts", import.meta.url), "utf8");
  assert.match(client, /getCanonicalProjectEvents/);
  assert.match(client, /\/events/);
});
