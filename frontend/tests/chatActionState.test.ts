import assert from "node:assert/strict";
import test from "node:test";

import { actionFromPayload } from "../src/state/chatActionState.ts";
import {
  assignmentAnalysisFromActionPayload,
  assignmentWorkspaceActionFromPayload,
} from "../src/state/assignmentWorkspaceState.ts";

test("maps the persisted backend action payload to the shared chat action model", () => {
  const action = actionFromPayload({
    action_type: "command",
    title: "Run the project test suite",
    summary: "Runs tests.",
    steps: ["Approve", "Run"],
    safety_information: { approval_required: true },
    status: "awaiting_approval",
    approval_required: true,
    technical_details: {
      command_plan: { plan_id: "safe", command: "python -m pytest -q" },
    },
  });
  assert.equal(action?.status, "awaiting_approval");
  assert.equal(action?.commandPlan?.command, "python -m pytest -q");
  assert.equal(action?.approvalRequired, true);
});

const assignmentPayload = {
  action_id: "assignment-action-1",
  action_type: "assignment",
  title: "Assignment 2",
  summary: "Create the workspace after review.",
  status: "awaiting_approval",
  technical_details: {
    assignment_analysis: {
      title: "Assignment 2",
      section_count: 1,
      task_count: 3,
      evidence_count: 4,
      report_section_count: 2,
      next_recommended_step: "Create the workspace after review.",
    },
    workspace_action: {
      action_id: "assignment-action-1",
      status: "awaiting_approval",
      targets: [{
        assignment_number: 2,
        assignment_title: "Assignment 2",
        workspace_path: "assignment_workspaces/assignment_2",
        generation_mode: "mixed",
        planned_file_count: 9,
      }],
      results: [],
    },
    copilot_result: {
      parsed_document_summary: { title: "Assignment 2" },
      workspace_generation_plan: [],
      grounded_file_blueprints: [],
      next_recommended_step: "Create the workspace after review.",
      tools_executed: false,
      files_written: false,
      training_performed: false,
    },
  },
};

test("maps persisted assignment-analysis payload to the card model", () => {
  const card = assignmentAnalysisFromActionPayload(assignmentPayload);
  assert.equal(card?.title, "Assignment 2");
  assert.equal(card?.summary, "Create the workspace after review.");
  assert.deepEqual(card?.rows.map((row) => row.value), ["1", "3", "4", "2"]);
  assert.equal(card?.technical.assignment_analysis["task_count"], 3);
});

test("maps persisted workspace action states", () => {
  const awaiting = assignmentWorkspaceActionFromPayload(assignmentPayload);
  assert.equal(awaiting?.actionId, "assignment-action-1");
  assert.equal(awaiting?.status, "awaiting_approval");
  assert.equal(awaiting?.targets[0].plannedFileCount, 9);

  const completed = assignmentWorkspaceActionFromPayload({
    ...assignmentPayload,
    status: "completed",
    result_summary: "Created 9 starter files in assignment_workspaces/assignment_2.",
    technical_details: {
      ...assignmentPayload.technical_details,
      workspace_action: {
        ...assignmentPayload.technical_details.workspace_action,
        status: "completed",
        result_summary: "Created 9 starter files in assignment_workspaces/assignment_2.",
        results: [{
          workspace_path: "assignment_workspaces/assignment_2",
          created_files: ["README.md"],
          skipped_files: [],
          conflicts: [],
          refused_files: [],
          grounding_summary: {},
          warnings: [],
          overwrite: false,
          commands_executed: false,
          generated_code_executed: false,
          generation_mode: "mixed",
        }],
      },
    },
  });
  assert.equal(completed?.status, "completed");
  assert.equal(completed?.results?.[0].created_files[0], "README.md");

  const cancelled = assignmentWorkspaceActionFromPayload({
    ...assignmentPayload,
    status: "cancelled",
    technical_details: {
      ...assignmentPayload.technical_details,
      workspace_action: {
        ...assignmentPayload.technical_details.workspace_action,
        status: "cancelled",
        result_summary: "Workspace creation cancelled. No files were written.",
      },
    },
  });
  assert.equal(cancelled?.status, "cancelled");
  assert.equal(cancelled?.resultSummary, "Workspace creation cancelled. No files were written.");
});
