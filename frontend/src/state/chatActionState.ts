import type { AssignmentCommandRecord } from "../clients/astraClient";

export type ChatActionStatus =
  | "awaiting_approval"
  | "approving"
  | "approved"
  | "running"
  | "scanning"
  | "completed"
  | "partially_completed"
  | "failed"
  | "cancelled";

export type ChatActionType =
  | "command"
  | "assignment"
  | "folder_access"
  | "project_plan"
  | "project_patch"
  | "project_rollback"
  | "project_command"
  | "system_configuration"
  | "report_generation"
  | "file_creation"
  | "dataset_operation";

export interface ChatAction {
  actionId?: string;
  actionType: ChatActionType;
  title: string;
  summary: string;
  steps: string[];
  safetyInformation: Record<string, unknown>;
  status: ChatActionStatus;
  approvalRequired: boolean;
  resultSummary?: string;
  technicalDetails: Record<string, unknown>;
  commandPlan?: AssignmentCommandRecord;
  options?: Array<{ id: string; label: string }>;
  selectedOption?: string;
  error?: string;
}

export function actionFromPayload(payload: Record<string, unknown>): ChatAction | null {
  const details = asObject(payload.technical_details);
  const plan = asObject(details.command_plan) as unknown as AssignmentCommandRecord;
  const actionType = readString(payload.action_type) as ChatActionType;
  if (!actionType || !readString(payload.title)) return null;
  return {
    actionId: readString(payload.action_id) || undefined,
    actionType,
    title: readString(payload.title),
    summary: readString(payload.summary),
    steps: readStrings(payload.steps),
    safetyInformation: asObject(payload.safety_information),
    status: (readString(payload.status, "awaiting_approval") as ChatActionStatus),
    approvalRequired: payload.approval_required === true,
    resultSummary: readString(payload.result_summary) || undefined,
    technicalDetails: details,
    commandPlan: readString(plan.plan_id) ? plan : undefined,
    error: readString(payload.error) || undefined,
  };
}

function asObject(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function readString(value: unknown, fallback = "") {
  return typeof value === "string" ? value : fallback;
}

function readStrings(value: unknown) {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}
