import type { ChatRequestRecord, ChatRunResponse } from "../clients/astraClient";

export interface StorageLike {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

export type StreamRecoveryStatus = "pending" | "active" | "interrupted" | "failed" | "cancelled";

export interface StreamRecoveryMarker {
  conversationId: string;
  requestId: string;
  startedAt: string;
  status: StreamRecoveryStatus;
}

export interface ScrollSnapshot {
  top: number;
  atBottom: boolean;
}

export const STREAM_RECOVERY_KEY = "astra.chat.streamRecovery.v1";
const SCROLL_KEY_PREFIX = "astra.chat.scroll.v1:";

export function isValidConversationId(value: string | null): value is string {
  return Boolean(value && /^[A-Za-z0-9._:-]{1,128}$/.test(value));
}

export function shouldClearActiveConversation(httpStatus: number): boolean {
  return httpStatus === 404;
}

export function canonicalConversationTurns<T extends Pick<ChatRunResponse, "run_id" | "created_at">>(turns: T[]): T[] {
  const unique = new Map<string, T>();
  for (const turn of turns) {
    if (turn && typeof turn.run_id === "string" && turn.run_id) unique.set(turn.run_id, turn);
  }
  return [...unique.values()].sort((left, right) => {
    const time = Date.parse(left.created_at) - Date.parse(right.created_at);
    return time || left.run_id.localeCompare(right.run_id);
  });
}

export function startStreamRecovery(
  storage: StorageLike,
  conversationId: string,
  requestId: string,
  startedAt = new Date().toISOString(),
): StreamRecoveryMarker {
  const marker: StreamRecoveryMarker = { conversationId, requestId, startedAt, status: "pending" };
  writeMarker(storage, marker);
  return marker;
}

export function updateStreamRecovery(
  storage: StorageLike,
  status: StreamRecoveryStatus,
): StreamRecoveryMarker | null {
  const marker = readStreamRecovery(storage);
  if (!marker) return null;
  const updated = { ...marker, status };
  writeMarker(storage, updated);
  return updated;
}

export function readStreamRecovery(storage: StorageLike): StreamRecoveryMarker | null {
  try {
    const value = JSON.parse(storage.getItem(STREAM_RECOVERY_KEY) ?? "null") as Partial<StreamRecoveryMarker> | null;
    if (!value || !isValidConversationId(value.conversationId ?? null)) return null;
    if (typeof value.requestId !== "string" || !value.requestId || Number.isNaN(Date.parse(value.startedAt ?? ""))) return null;
    if (!["pending", "active", "interrupted", "failed", "cancelled"].includes(String(value.status))) return null;
    return value as StreamRecoveryMarker;
  } catch {
    return null;
  }
}

export function clearStreamRecovery(storage: StorageLike): void {
  storage.removeItem(STREAM_RECOVERY_KEY);
}

export function resolveStreamRecovery(
  marker: StreamRecoveryMarker | null,
  conversationId: string,
  requests: Array<Pick<ChatRequestRecord, "request_id" | "status">>,
): "none" | "pending" | "active" | "interrupted" | "failed" | "cancelled" {
  if (!marker || marker.conversationId !== conversationId) return "none";
  const durable = requests.find((request) => request.request_id === marker.requestId);
  if (durable?.status === "completed") return "none";
  if (durable?.status === "pending" || durable?.status === "active") return durable.status;
  if (durable?.status === "failed" || durable?.status === "cancelled" || durable?.status === "interrupted") return durable.status;
  // Historical clients may have a browser marker without a durable request row.
  // That marker can describe an interruption, but it never authorizes a replay.
  return marker.status === "pending" || marker.status === "active" ? "interrupted" : marker.status;
}

export function readScrollSnapshot(storage: StorageLike, conversationId: string): ScrollSnapshot | null {
  try {
    const value = JSON.parse(storage.getItem(`${SCROLL_KEY_PREFIX}${conversationId}`) ?? "null") as Partial<ScrollSnapshot> | null;
    if (!value || typeof value.top !== "number" || !Number.isFinite(value.top) || typeof value.atBottom !== "boolean") return null;
    return { top: Math.max(0, value.top), atBottom: value.atBottom };
  } catch {
    return null;
  }
}

export function writeScrollSnapshot(storage: StorageLike, conversationId: string, snapshot: ScrollSnapshot): void {
  storage.setItem(`${SCROLL_KEY_PREFIX}${conversationId}`, JSON.stringify({
    top: Math.max(0, snapshot.top),
    atBottom: snapshot.atBottom,
  }));
}

export function clearScrollSnapshot(storage: StorageLike, conversationId: string | null): void {
  if (conversationId) storage.removeItem(`${SCROLL_KEY_PREFIX}${conversationId}`);
}

function writeMarker(storage: StorageLike, marker: StreamRecoveryMarker): void {
  storage.setItem(STREAM_RECOVERY_KEY, JSON.stringify(marker));
}
