import assert from "node:assert/strict";
import test from "node:test";

import { actionFromPayload } from "../src/state/chatActionState.ts";
import {
  assignmentAnalysisFromActionPayload,
  assignmentWorkspaceActionFromPayload,
} from "../src/state/assignmentWorkspaceState.ts";
import { folderAccessActionFromPayload } from "../src/state/folderAccessState.ts";

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

const folderPayload = {
  action_id: "folder-action-1",
  action_type: "folder_access",
  title: "Folder access requested",
  summary: "Approve read-only access before Astra scans this folder.",
  status: "awaiting_approval",
  technical_details: {
    folder_action: {
      action_id: "folder-action-1",
      status: "awaiting_approval",
      requested_path: "/home/user/project",
      display_path: "user/project",
      inventory: [],
      summary: {
        total_discovered: 0,
        readable: 0,
        ignored: 0,
        assignments: 0,
        datasets: 0,
        source_files: 0,
        reports: 0,
        evidence_files: 0,
        configuration_files: 0,
        other_files: 0,
        warning_count: 0,
      },
      diff: { added: 0, changed: 0, deleted: 0, unchanged: 0 },
      warnings: [],
      scan_count: 0,
    },
  },
};

test("maps persisted folder action states without exposing absolute scanned files", () => {
  const awaiting = folderAccessActionFromPayload(folderPayload);
  assert.equal(awaiting?.actionId, "folder-action-1");
  assert.equal(awaiting?.status, "awaiting_approval");
  assert.equal(awaiting?.requestedDisplayPath, "user/project");

  const completed = folderAccessActionFromPayload({
    ...folderPayload,
    status: "completed",
    result_summary: "Scanned 2 readable files.",
    technical_details: {
      folder_action: {
        ...folderPayload.technical_details.folder_action,
        status: "completed",
        approved_root: "/home/user/project",
        approved_root_display: "user/project",
        last_scanned_at: "2026-07-12T12:00:00+00:00",
        scan_count: 1,
        result_summary: "Scanned 2 readable files.",
        summary: {
          total_discovered: 3,
          readable: 2,
          ignored: 1,
          assignments: 1,
          datasets: 0,
          source_files: 1,
          reports: 0,
          evidence_files: 0,
          configuration_files: 0,
          other_files: 0,
          warning_count: 0,
        },
        diff: { added: 2, changed: 0, deleted: 0, unchanged: 0 },
        inventory: [
          { relative_path: "assignment.md", filename: "assignment.md", classification: "assignment", extension: ".md", size_bytes: 120, modified_at: "now", fingerprint: "a", status: "readable" },
          { relative_path: "/home/user/project/secret.py", filename: "secret.py", classification: "source_code", extension: ".py", size_bytes: 10, modified_at: "now", fingerprint: "b", status: "readable" },
        ],
      },
    },
  });
  assert.equal(completed?.status, "completed");
  assert.equal(completed?.summary.readable, 2);
  assert.equal(completed?.diff.added, 2);
  assert.deepEqual(completed?.inventory.map((item) => item.relativePath), ["assignment.md"]);

  const cancelled = folderAccessActionFromPayload({
    ...folderPayload,
    status: "cancelled",
    technical_details: {
      folder_action: {
        ...folderPayload.technical_details.folder_action,
        status: "cancelled",
        result_summary: "Folder access cancelled. No folder was scanned.",
      },
    },
  });
  assert.equal(cancelled?.status, "cancelled");
  assert.equal(cancelled?.resultSummary, "Folder access cancelled. No folder was scanned.");
});

test("maps failed folder actions to a friendly card error", () => {
  const failed = folderAccessActionFromPayload({
    ...folderPayload,
    status: "failed",
    error: "Folder path must point to a directory.",
    technical_details: {
      folder_action: {
        ...folderPayload.technical_details.folder_action,
        status: "failed",
        error: "Folder path must point to a directory.",
      },
    },
  });
  assert.equal(failed?.status, "failed");
  assert.equal(failed?.error, "Folder path must point to a directory.");
});
