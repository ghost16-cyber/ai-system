import type { ChatRunResponse, ChatStreamEvent } from "../clients/astraClient";

export function actionRunFromStreamEvent(event: ChatStreamEvent): ChatRunResponse | null {
  if (event.event !== "action_required" && ![
    "project_job_created", "project_job_updated", "project_analysis_completed", "project_impact_ready",
    "project_synthesis_started", "model_generation_started", "model_generation_completed",
    "model_response_normalized", "model_synthesis_confidence_evaluated", "project_synthesis_blocked",
    "project_validation_failed", "project_diagnosis_offered", "project_diagnosis_started",
    "project_diagnosis_completed", "project_diagnosis_clarification", "project_repair_ready",
    "project_repair_blocked", "project_repair_applied", "project_repair_validated",
    "project_delivery_updated", "client_engagement_updated", "project_validation_updated",
  ].includes(event.event)) return null;
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
