import assert from "node:assert/strict";
import test from "node:test";

import {
  approveAndExecuteCommand,
  commandResultPresentation,
  finalRelevantLines,
  readablePytestSummary,
  tryLockCommandAction,
} from "../src/state/chatCommandState.ts";

test("renders successful pytest totals as a readable sentence", () => {
  assert.equal(readablePytestSummary("567 passed in 12.40s"), "567 tests passed in 12.40 seconds.");
});

test("renders mixed pytest totals and retains a concise failure tail", () => {
  assert.equal(
    readablePytestSummary("2 failed, 5 passed, 1 skipped in 1.25s"),
    "Pytest finished with 2 failed, 5 passed, and 1 skipped in 1.25 seconds.",
  );
  assert.equal(finalRelevantLines("one\ntwo\nthree", 2), "two\nthree");
});

test("uses pytest output for the command result summary", () => {
  const result = commandResultPresentation({
    action: "pytest",
    display_state: "completed",
    exit_code: 0,
    stdout: "3 passed in 0.04s",
    stderr: "",
  } as never);
  assert.equal(result.summary, "3 tests passed in 0.04 seconds.");
  assert.equal(result.errorTail, "");
});

test("approval completes before one execution request is made", async () => {
  const events: string[] = [];
  const executed = { display_state: "completed", exit_code: 0 } as never;
  const result = await approveAndExecuteCommand({
    planId: "safe-plan",
    association: { assignment_id: "chat-action", workspace_path: "." },
    calls: {
      approve: async (_planId, request) => {
        events.push(`approve:${String(request.confirmation)}`);
        return { plan: {} as never, approval_token: "single-use-token" };
      },
      execute: async (_planId, request) => {
        events.push(`execute:${String(request.approval_token)}`);
        return executed;
      },
    },
    onApproved: () => events.push("approved"),
    onRunning: () => events.push("running"),
  });
  assert.equal(result, executed);
  assert.deepEqual(events, [
    "approve:APPROVE safe-plan",
    "approved",
    "running",
    "execute:single-use-token",
  ]);
});

test("a synchronous action lock rejects duplicate clicks", () => {
  const locks = new Set<string>();
  assert.equal(tryLockCommandAction(locks, "safe-plan"), true);
  assert.equal(tryLockCommandAction(locks, "safe-plan"), false);
});


test("forwards the persistent chat run ID through approval and execution", async () => {
  const requests: Array<Record<string, unknown>> = [];

  await approveAndExecuteCommand({
    planId: "safe-plan",
    association: {
      assignment_id: "chat-action",
      workspace_path: ".",
      chat_run_id: "chat-run-123",
    },
    calls: {
      approve: async (_planId, request) => {
        requests.push(request);
        return {
          plan: {} as never,
          approval_token: "single-use-token",
        };
      },
      execute: async (_planId, request) => {
        requests.push(request);
        return {
          display_state: "completed",
          exit_code: 0,
        } as never;
      },
    },
    onApproved: () => undefined,
    onRunning: () => undefined,
  });

  assert.equal(requests.length, 2);
  assert.equal(requests[0].chat_run_id, "chat-run-123");
  assert.equal(requests[1].chat_run_id, "chat-run-123");
});

