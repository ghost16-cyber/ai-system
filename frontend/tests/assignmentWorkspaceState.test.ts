import assert from "node:assert/strict";
import test from "node:test";

import {
  deriveAssignmentWorkspaceTargets,
  isAssignmentWorkspaceRequest,
  presentAssignmentWorkspaceResults,
} from "../src/state/assignmentWorkspaceState.ts";

const copilotResult = {
  workspace_generation_plan: [
    {
      assignment_number: 1,
      assignment_title: "Assignment 1",
      workspace_path: "assignment_workspaces/assignment_1",
      generation_mode: "mixed",
      files: [{ path: "README.md" }, { path: "producer.py" }],
    },
  ],
  generation_mode: "mixed",
} as never;

test("recognizes chat-native workspace creation requests", () => {
  assert.equal(isAssignmentWorkspaceRequest("create assignment workspace and tell me where"), true);
  assert.equal(isAssignmentWorkspaceRequest("build the assignment workspace"), true);
  assert.equal(isAssignmentWorkspaceRequest("explain what a workspace is"), false);
});

test("derives safe workspace targets from the copilot generation plan", () => {
  assert.deepEqual(deriveAssignmentWorkspaceTargets(copilotResult), [
    {
      assignmentNumber: 1,
      assignmentTitle: "Assignment 1",
      workspacePath: "assignment_workspaces/assignment_1",
      generationMode: "mixed",
      plannedFileCount: 2,
    },
  ]);
});

test("workspace result presentation only claims files returned by the backend", () => {
  const presentation = presentAssignmentWorkspaceResults([
    {
      workspace_path: "assignment_workspaces/assignment_1",
      created_files: ["README.md", "producer.py"],
      skipped_files: [],
      conflicts: [],
      refused_files: [],
      grounding_summary: {},
      warnings: [],
      overwrite: false,
      commands_executed: false,
      generated_code_executed: false,
      generation_mode: "mixed",
    },
  ]);

  assert.equal(presentation.status, "completed");
  assert.equal(presentation.createdFileCount, 2);
  assert.match(presentation.summary, /Created 2 starter files/);
});
