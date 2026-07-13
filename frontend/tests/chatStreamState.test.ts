import assert from "node:assert/strict";
import test from "node:test";

import { folderAccessActionFromPayload } from "../src/state/folderAccessState.ts";
import { actionRunFromStreamEvent } from "../src/state/chatStreamState.ts";

test("an action-only stream event produces the pending folder card state", () => {
  const action = {
    action_id: "folder-1",
    action_type: "folder_access",
    title: "Folder access requested",
    summary: "Approve read-only access before Astra scans this folder.",
    status: "awaiting_approval",
    approval_required: true,
    technical_details: {
      folder_action: {
        action_id: "folder-1",
        status: "awaiting_approval",
        requested_path: "/home/palla/projects/test-folder",
        display_path: "projects/test-folder",
        inventory: [],
        summary: {},
        diff: {},
        warnings: [],
        scan_count: 0,
      },
    },
  };
  const run = actionRunFromStreamEvent({
    event: "action_required",
    data: {
      run: {
        run_id: "run-1",
        conversation_id: "conversation-1",
        user_message: "Connect the project folder /home/palla/projects/test-folder",
        assistant_response: "Approve read-only folder access before I scan that project folder.",
        selected_specialist: "folder_access",
        intent: "folder_access",
        confidence: 1,
        rag_used: false,
        rag_skip_reason: "Direct folder access action intercepted before retrieval.",
        rag_context_count: 0,
        runtime_decision: "approval_required",
        safety_decision: "approval_required",
        used_real_slm: false,
        slm_provider: "not_invoked",
        slm_model: null,
        slm_fallback_reason: "Direct folder access did not require model generation.",
        slm_latency_ms: null,
        memory_used: false,
        memory_summary: null,
        created_at: "2026-07-12T12:00:00Z",
        trace_summary: [],
        action,
      },
      action,
    },
  });

  assert.equal(run?.run_id, "run-1");
  assert.equal(run?.conversation_id, "conversation-1");
  const card = folderAccessActionFromPayload(run?.action);
  assert.equal(card?.actionId, "folder-1");
  assert.equal(card?.status, "awaiting_approval");
  assert.equal(card?.inventory.length, 0);
});

test("response deltas are not misclassified as action events", () => {
  assert.equal(actionRunFromStreamEvent({ event: "response_delta", data: { delta: "hello" } }), null);
});
