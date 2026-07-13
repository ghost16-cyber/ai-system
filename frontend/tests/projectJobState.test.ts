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
