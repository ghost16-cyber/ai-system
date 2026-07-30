import assert from "node:assert/strict";
import test from "node:test";

import {
  canonicalConversationTurns,
  clearScrollSnapshot,
  clearStreamRecovery,
  isValidConversationId,
  readScrollSnapshot,
  readStreamRecovery,
  resolveStreamRecovery,
  shouldClearActiveConversation,
  startStreamRecovery,
  updateStreamRecovery,
  writeScrollSnapshot,
} from "../src/state/conversationReloadState.ts";

class MemoryStorage {
  values = new Map<string, string>();
  getItem(key: string) { return this.values.get(key) ?? null; }
  setItem(key: string, value: string) { this.values.set(key, value); }
  removeItem(key: string) { this.values.delete(key); }
}

test("valid conversation turns restore exactly once in deterministic order", () => {
  const turns = canonicalConversationTurns([
    { run_id: "run-b", created_at: "2026-07-17T10:00:02Z" },
    { run_id: "run-a", created_at: "2026-07-17T10:00:01Z" },
    { run_id: "run-a", created_at: "2026-07-17T10:00:01Z" },
  ]);
  assert.deepEqual(turns.map((turn) => turn.run_id), ["run-a", "run-b"]);
});

test("malformed and missing active conversation IDs are rejected", () => {
  assert.equal(isValidConversationId(null), false);
  assert.equal(isValidConversationId("../../other-workspace"), false);
  assert.equal(isValidConversationId("conversation-123"), true);
});

test("only a definitive not-found clears an active backend conversation identity", () => {
  assert.equal(shouldClearActiveConversation(404), true);
  assert.equal(shouldClearActiveConversation(503), false);
  assert.equal(shouldClearActiveConversation(500), false);
  assert.equal(shouldClearActiveConversation(409), false);
});

test("active stream reload is interrupted without replay and backend completion wins", () => {
  const storage = new MemoryStorage();
  const marker = startStreamRecovery(storage, "conversation-1", "request-1", "2026-07-17T10:00:00Z");
  assert.equal(marker.status, "pending");
  assert.equal(resolveStreamRecovery(marker, "conversation-1", []), "interrupted");
  assert.equal(updateStreamRecovery(storage, "active")?.status, "active");
  assert.equal(
    resolveStreamRecovery(marker, "conversation-1", [{ request_id: "request-1", status: "completed" }]),
    "none",
  );
  assert.equal(
    resolveStreamRecovery(marker, "conversation-1", [{ request_id: "request-1", status: "active" }]),
    "active",
  );
  assert.equal(resolveStreamRecovery(marker, "different-conversation", []), "none");
});

test("failed and cancelled streams retain bounded recovery status until cleared", () => {
  const storage = new MemoryStorage();
  startStreamRecovery(storage, "conversation-1", "request-1", "2026-07-17T10:00:00Z");
  const failed = updateStreamRecovery(storage, "failed");
  assert.equal(resolveStreamRecovery(failed, "conversation-1", [{ request_id: "request-1", status: "failed" }]), "failed");
  const cancelled = updateStreamRecovery(storage, "cancelled");
  assert.equal(resolveStreamRecovery(cancelled, "conversation-1", [{ request_id: "request-1", status: "cancelled" }]), "cancelled");
  clearStreamRecovery(storage);
  assert.equal(readStreamRecovery(storage), null);
});

test("scroll state preserves an intentional reading position and can be reset", () => {
  const storage = new MemoryStorage();
  writeScrollSnapshot(storage, "conversation-1", { top: 480, atBottom: false });
  assert.deepEqual(readScrollSnapshot(storage, "conversation-1"), { top: 480, atBottom: false });
  clearScrollSnapshot(storage, "conversation-1");
  assert.equal(readScrollSnapshot(storage, "conversation-1"), null);
});
