import type { ChatRunResponse, ChatStreamEvent } from "../clients/astraClient";

export function actionRunFromStreamEvent(event: ChatStreamEvent): ChatRunResponse | null {
  if (event.event !== "action_required") return null;
  const run = event.data.run;
  if (!run || typeof run !== "object" || Array.isArray(run)) return null;
  const candidate = run as Partial<ChatRunResponse>;
  return typeof candidate.run_id === "string"
    && typeof candidate.conversation_id === "string"
    && candidate.action
    && typeof candidate.action === "object"
    ? candidate as ChatRunResponse
    : null;
}
