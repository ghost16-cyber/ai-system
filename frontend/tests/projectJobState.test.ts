import assert from "node:assert/strict";
import test from "node:test";

import {
  mergeProjectJobAction,
  projectJobActionFromPayload,
} from "../src/state/projectJobState.ts";

function payload(status = "planned") {
  return {
    action_type: "project_job",
    technical_details: {
      project_job: {
        job_id: "job-1",
        status,
        objective: "Implement the README feature",
        deliverables: ["Approved implementation"],
        constraints: ["No deployment"],
        acceptance_criteria: ["Tests pass (test_app.py)"],
        relevant_paths: ["README.md", "app.py", "/home/palla/secret.py", "../escape.py"],
        missing_information: [],
        risks: ["Project files are evidence"],
        clarification: null,
        implementation_plan: {
          current_state_findings: [{ claim: "Feature is missing", relative_path: "app.py" }],
          files_likely_involved: ["app.py"],
          steps: ["Prepare an immutable patch"],
          safety_impact: "Read-only plan",
          unresolved_assumptions: [],
        },
        analysis: {
          status: "completed",
          structural_findings: [{ relative_path: "api.py", summary: "function; 1 route", parse_status: "complete" }],
          relevant_symbols: [{ relative_path: "api.py", name: "get_items", kind: "function", range: { start_line: 6, end_line: 7 } }],
          coherent_file_set: [
            { relative_path: "api.py", classification: "required", reason: "route handler" },
            { relative_path: "/home/palla/private.py", classification: "required", reason: "unsafe" },
          ],
          impacted_tests: ["test_api.py", "../escape.test.ts"],
          confidence: { level: "medium", warnings: ["One dynamic call is unresolved."] },
          plan_only: false,
          plan_only_reasons: [],
          prevalidation: { status: "passed", checks: ["virtual syntax"], warnings: [] },
        },
        synthesis: {
          status: "validated",
          strategy: "model_assisted",
          provider: "fake",
          model: "fake-project-synthesizer-v1",
          contract_version: "astra.project-synthesis.response.v1",
          evidence: { file_count: 3, excerpt_count: 4, excerpt_chars: 900 },
          confidence: { level: "medium", reasons: ["Stage 6 validation passed."], model_claim: "high" },
          warnings: ["Review one assumption."],
          assumptions: ["The endpoint remains synchronous."],
          requires_clarification: false,
          summary: "Added bounded pagination.",
        },
        repair: {
          status: "offered",
          repair_chain_id: "chain-1",
          repair_cycle_id: "cycle-1",
          cycle_number: 1,
          failure_evidence_id: "evidence-1",
          command_execution_id: "execution-1",
          diagnosis_strategy: "deterministic",
          confidence: { level: "high", reasons: ["One deterministic root cause is directly supported."] },
          root_causes: [{
            reason_code: "python_syntax_error",
            explanation: "The approved validation found a syntax error.",
            affected_files: ["api.py", "/private.py"],
            affected_symbols: ["get_items"],
          }],
          affected_files: ["api.py", "../escape.py"],
          assumptions: ["The route remains synchronous."],
          warnings: ["failure_output_truncated"],
          failed_command_summary: "One test failed.",
          failure_output_truncated: true,
          failure_redaction_count: 2,
          validation_rerun_status: "not_planned",
          rollback_available: false,
        },
        patch_ids: [],
        command_plan_ids: [],
        validation_plan: [{ action: "pytest" }],
        validation_results: [],
        completion_summary: null,
        revision_count: 0,
        max_revision_cycles: 3,
      },
    },
  };
}

test("project job action renders bounded relative evidence", () => {
  const action = projectJobActionFromPayload(payload());
  assert.ok(action);
  assert.equal(action.jobId, "job-1");
  assert.equal(action.status, "planned");
  assert.deepEqual(action.relevantPaths, ["README.md", "app.py"]);
  assert.deepEqual(action.plan.files, ["app.py"]);
  assert.equal(action.analysis.confidence, "medium");
  assert.deepEqual(action.analysis.coherentFiles.map((item) => item.relativePath), ["api.py"]);
  assert.deepEqual(action.analysis.impactedTests, ["test_api.py"]);
  assert.deepEqual(action.analysis.symbols[0], { relativePath: "api.py", name: "get_items", kind: "function", startLine: 6, endLine: 7 });
  assert.equal(action.analysis.prevalidation.status, "passed");
  assert.equal(action.synthesis.strategy, "model_assisted");
  assert.equal(action.synthesis.confidence, "medium");
  assert.equal(action.synthesis.modelClaim, "high");
  assert.deepEqual(action.synthesis.evidence, { fileCount: 3, excerptCount: 4, excerptChars: 900 });
  assert.equal(action.repair.status, "offered");
  assert.equal(action.repair.cycleNumber, 1);
  assert.equal(action.repair.confidence, "high");
  assert.deepEqual(action.repair.affectedFiles, ["api.py"]);
  assert.deepEqual(action.repair.rootCauses[0].affectedFiles, ["api.py"]);
  assert.equal(action.repair.outputTruncated, true);
  assert.equal(action.repair.redactionCount, 2);
});

test("job updates replace the same persisted object", () => {
  const planned = projectJobActionFromPayload(payload());
  const completed = projectJobActionFromPayload({
    ...payload("completed"),
    technical_details: {
      project_job: {
        ...payload("completed").technical_details.project_job,
        completion_summary: { files_changed: ["app.py"], rollback_available: true },
      },
    },
  });
  const merged = mergeProjectJobAction(planned ?? undefined, completed);
  assert.equal(merged?.jobId, "job-1");
  assert.equal(merged?.status, "completed");
  assert.deepEqual(merged?.completionSummary?.files_changed, ["app.py"]);
});

test("malformed and unrelated actions are ignored", () => {
  assert.equal(projectJobActionFromPayload({ action_type: "project_patch" }), null);
  assert.equal(projectJobActionFromPayload({ action_type: "project_job", technical_details: {} }), null);
});

test("stage 9 execution bridge jobs stay hidden behind the delivery card", () => {
  const bridge = payload();
  bridge.technical_details.project_job.delivery_job_id = "delivery-1";
  assert.equal(projectJobActionFromPayload(bridge), null);
});

test("low confidence plan-only analysis blocks preview state and remains bounded", () => {
  const raw = payload();
  raw.technical_details.project_job.analysis.confidence.level = "low";
  raw.technical_details.project_job.analysis.plan_only = true;
  raw.technical_details.project_job.analysis.plan_only_reasons = ["Lexical fallback cannot resolve dynamic imports."];
  const action = projectJobActionFromPayload(raw);
  assert.equal(action?.analysis.planOnly, true);
  assert.deepEqual(action?.analysis.planOnlyReasons, ["Lexical fallback cannot resolve dynamic imports."]);
});
