import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  clientEngagementActionFromPayload,
  exactScopeApprovalRequest,
  mergeClientEngagementAction,
} from "../src/state/clientEngagementState.ts";

function payload(state = "awaiting_scope_approval") {
  return {
    action_type: "client_engagement",
    technical_details: { client_engagement: {
      schema_version: "astra.client-engagement.v1", engagement_id: "engagement-1",
      conversation_id: "conversation-1", state, state_version: 4,
      understood_outcome: "Build a restaurant website.",
      authorized_evidence: [{ evidence_id: "ev-1", source_type: "original_chat_request", label: "Original request", is_stale: false }],
      missing_information: [], pending_questions: [], approved_scope_revision_id: null,
      current_scope_revision: {
        revision_id: "revision-1", revision_number: 1, scope_hash: "a".repeat(64),
        scope: {
          engagement_title: "Restaurant website", problem_statement: "The restaurant needs a website.", desired_outcome: "Build a restaurant website.",
          deliverables: [{ deliverable_id: "deliverable-01", title: "Responsive website", description: "Mobile and desktop website.", acceptance_criteria: [{ criterion_id: "criterion-01", statement: "No horizontal overflow.", review_mode: "automated" }] }],
          functional_requirements: [{ text: "Include a menu page" }], non_functional_requirements: [{ text: "Responsive" }],
          milestones: [{ title: "Website", completion_signal: "Criteria recorded" }],
          assumptions: [{ text: "Local delivery only" }], exclusions: [{ text: "No external deployment" }],
          risks: [{ description: "Form endpoint is unknown" }],
          client_responsibilities: ["Supply assets"], astra_responsibilities: ["Preserve approvals"],
          effort_estimate: { relative_size: "medium", estimated_work_unit_count: 6, expected: { minimum: 5, maximum: 8 }, pessimistic: { minimum: 8, maximum: 12 }, confidence: "medium", uncertainty_drivers: ["Form integration"] },
        },
      },
      project_launch: null, scope_changes: [], limitation: null,
    } },
  };
}

test("intake and scope preview normalize every client-facing section", () => {
  const action = clientEngagementActionFromPayload(payload());
  assert.ok(action?.scope);
  assert.equal(action.outcome, "Build a restaurant website.");
  assert.equal(action.evidence[0].label, "Original request");
  assert.equal(action.scope.deliverables[0].criteria[0].statement, "No horizontal overflow.");
  assert.deepEqual(action.scope.assumptions, ["Local delivery only"]);
  assert.deepEqual(action.scope.exclusions, ["No external deployment"]);
  assert.equal(action.scope.estimate?.expected, "5–8 work units");
});

test("exact-scope approval binds revision, hash, conversation, and version", () => {
  const action = clientEngagementActionFromPayload(payload());
  assert.ok(action);
  assert.deepEqual(exactScopeApprovalRequest(action), {
    conversation_id: "conversation-1", expected_state_version: 4,
    revision_id: "revision-1", scope_hash: "a".repeat(64),
  });
  assert.equal(exactScopeApprovalRequest({ ...action, status: "scope_approved" }), null);
});

test("clarification, launch, stale state, and scope change render safely", () => {
  const value = payload("clarification_required");
  const engagement = value.technical_details.client_engagement as Record<string, unknown>;
  engagement.pending_questions = [{ question_id: "q-1", semantic_key: "deployment", question: "Where should it be delivered?", rationale: "Changes scope", blocking: true, priority: "blocking" }];
  engagement.current_scope_revision = null;
  const clarified = clientEngagementActionFromPayload(value);
  assert.equal(clarified?.questions[0].blocking, true);

  const launchedValue = payload("project_launched");
  const launchedPublic = launchedValue.technical_details.client_engagement as Record<string, unknown>;
  launchedPublic.project_launch = { delivery_job_id: "delivery-1", scope_revision_id: "revision-1" };
  launchedPublic.scope_changes = [{ classification: "material_scope_addition", requested_change: "Add accounts", estimate_impact: "Higher", risk_impact: "Security review", resulting_revision_id: "revision-2" }];
  const launched = clientEngagementActionFromPayload(launchedValue);
  assert.equal(launched?.launch?.deliveryJobId, "delivery-1");
  assert.equal(launched?.scopeChanges[0].classification, "material_scope_addition");
});

test("reload and streaming duplicates replace one engagement card", () => {
  const first = clientEngagementActionFromPayload(payload());
  const second = clientEngagementActionFromPayload(payload("scope_approved"));
  assert.ok(first && second);
  assert.equal(mergeClientEngagementAction(first, second)?.status, "scope_approved");
});

test("Stage 10 remains one accessible chat with raw data collapsed", () => {
  const app = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");
  const client = readFileSync(new URL("../src/clients/astraClient.ts", import.meta.url), "utf8");
  const stream = readFileSync(new URL("../src/state/chatStreamState.ts", import.meta.url), "utf8");
  assert.match(app, /ClientEngagementCard/);
  assert.match(app, /What Astra understood/);
  assert.match(app, /Authorized evidence/);
  assert.match(app, /Approve exact scope/);
  assert.match(app, /Use reasonable assumptions/);
  assert.match(app, /Stage 9 project created/);
  assert.match(app, /Request a scope change/);
  assert.match(app, /synchronousLock/);
  assert.match(app, /<details className="technical">/);
  assert.doesNotMatch(app, /EngagementDashboard|ClientEngagementPage|ScopeConsole/);
  assert.match(client, /\/chat\/client-engagements/);
  assert.match(stream, /client_engagement_updated/);
});
