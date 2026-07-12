import type {
  AssignmentCopilotResult,
  AssignmentGenerationMode,
  AssignmentWorkspaceWriteResult,
} from "../clients/astraClient";
import type { ChatActionStatus } from "./chatActionState";

export interface AssignmentWorkspaceTarget {
  assignmentNumber: number;
  assignmentTitle: string;
  workspacePath: string;
  generationMode: AssignmentGenerationMode;
  plannedFileCount: number;
}

export interface AssignmentAnalysisCard {
  title: string;
  summary: string;
  rows: Array<{ label: string; value: string }>;
  technical: Record<string, unknown>;
  copilotResult?: AssignmentCopilotResult;
}

export interface AssignmentWorkspaceAction {
  actionId?: string;
  status: ChatActionStatus;
  targets: AssignmentWorkspaceTarget[];
  copilotResult: AssignmentCopilotResult;
  results?: AssignmentWorkspaceWriteResult[];
  resultSummary?: string;
  error?: string;
}

export interface AssignmentWorkspacePresentation {
  status: "completed" | "partially_completed";
  summary: string;
  createdFileCount: number;
  locations: string[];
}

const WORKSPACE_REQUEST_PHRASES = [
  "create assignment workspace",
  "create the assignment workspace",
  "create workspace for this assignment",
  "create the starter files",
  "set up this assignment",
  "setup this assignment",
  "build the assignment workspace",
];

export function isAssignmentWorkspaceRequest(normalizedPrompt: string): boolean {
  return WORKSPACE_REQUEST_PHRASES.some((phrase) => normalizedPrompt.includes(phrase));
}

export function deriveAssignmentWorkspaceTargets(
  result: AssignmentCopilotResult,
): AssignmentWorkspaceTarget[] {
  const plans = Array.isArray(result.workspace_generation_plan)
    ? result.workspace_generation_plan
    : [];

  const targets = plans.flatMap((plan) => {
    const assignmentNumber = readPositiveInteger(plan.assignment_number);
    const workspacePath = readString(plan.workspace_path);
    if (!assignmentNumber || !workspacePath) return [];

    const generationMode = isGenerationMode(plan.generation_mode)
      ? plan.generation_mode
      : (isGenerationMode(result.generation_mode) ? result.generation_mode : "mixed");

    return [{
      assignmentNumber,
      assignmentTitle: readString(plan.assignment_title) || `Assignment ${assignmentNumber}`,
      workspacePath,
      generationMode,
      plannedFileCount: Array.isArray(plan.files) ? plan.files.length : 0,
    }];
  });

  return targets.filter((target, index) => targets.findIndex(
    (candidate) => candidate.assignmentNumber === target.assignmentNumber
      && candidate.workspacePath === target.workspacePath,
  ) === index);
}

export function presentAssignmentWorkspaceResults(
  results: AssignmentWorkspaceWriteResult[],
): AssignmentWorkspacePresentation {
  const createdFileCount = results.reduce(
    (total, result) => total + result.created_files.length,
    0,
  );
  const locations = results.map((result) => result.workspace_path);
  const hasIssues = results.some((result) =>
    result.refused_files.length > 0 || result.conflicts.length > 0,
  );

  const locationText = locations.length === 1
    ? locations[0]
    : `${locations.length} assignment workspaces`;
  const summary = createdFileCount > 0
    ? `Created ${createdFileCount} starter file${createdFileCount === 1 ? "" : "s"} in ${locationText}.`
    : `No new files were created in ${locationText}. Existing or refused files are listed in the details.`;

  return {
    status: hasIssues ? "partially_completed" : "completed",
    summary,
    createdFileCount,
    locations,
  };
}

export function assignmentAnalysisFromActionPayload(
  payload: Record<string, unknown> | null | undefined,
): AssignmentAnalysisCard | null {
  if (!payload || payload.action_type !== "assignment") return null;
  const details = asRecord(payload.technical_details);
  if (!details) return null;
  const analysis = asRecord(details.assignment_analysis);
  if (!analysis) return null;
  const title = readString(analysis.title) || "Assignment analysis";
  const summary = readString(analysis.next_recommended_step) || readString(payload.summary);
  const copilot = asRecord(details.copilot_result);
  const card: AssignmentAnalysisCard = {
    title,
    summary,
    rows: [
      { label: "Sections found", value: String(readNumber(analysis.section_count)) },
      { label: "Tasks found", value: String(readNumber(analysis.task_count)) },
      { label: "Evidence required", value: String(readNumber(analysis.evidence_count)) },
      { label: "Report sections", value: String(readNumber(analysis.report_section_count)) },
    ],
    technical: {
      assignment_analysis: analysis,
      copilot_result: copilot ?? {},
    },
  };
  if (copilot) card.copilotResult = copilot as unknown as AssignmentCopilotResult;
  return card;
}

export function assignmentWorkspaceActionFromPayload(
  payload: Record<string, unknown> | null | undefined,
): AssignmentWorkspaceAction | null {
  if (!payload || payload.action_type !== "assignment") return null;
  const details = asRecord(payload.technical_details);
  if (!details) return null;
  const workspace = asRecord(details.workspace_action);
  const copilot = asRecord(details.copilot_result) as unknown as AssignmentCopilotResult | null;
  if (!workspace || !copilot) return null;
  const targets = Array.isArray(workspace.targets)
    ? workspace.targets.flatMap((item) => {
      const target = asRecord(item);
      if (!target) return [];
      const assignmentNumber = readNumber(target.assignment_number);
      const workspacePath = readString(target.workspace_path);
      if (!assignmentNumber || !workspacePath) return [];
      return [{
        assignmentNumber,
        assignmentTitle: readString(target.assignment_title) || `Assignment ${assignmentNumber}`,
        workspacePath,
        generationMode: isGenerationMode(target.generation_mode) ? target.generation_mode : "mixed",
        plannedFileCount: readNumber(target.planned_file_count),
      }];
    })
    : [];
  return {
    actionId: readString(workspace.action_id) || readString(payload.action_id) || undefined,
    status: readStatus(workspace.status) || readStatus(payload.status) || "awaiting_approval",
    targets,
    copilotResult: copilot,
    results: Array.isArray(workspace.results) ? workspace.results as AssignmentWorkspaceWriteResult[] : undefined,
    resultSummary: readString(workspace.result_summary) || readString(payload.result_summary) || undefined,
    error: readString(workspace.error) || readString(payload.error) || undefined,
  };
}

function readPositiveInteger(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) && value > 0
    ? value
    : null;
}

function readString(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function readNumber(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function readStatus(value: unknown): ChatActionStatus | null {
  return typeof value === "string" && [
    "awaiting_approval",
    "approving",
    "approved",
    "running",
    "completed",
    "partially_completed",
    "failed",
    "cancelled",
  ].includes(value)
    ? value as ChatActionStatus
    : null;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function isGenerationMode(value: unknown): value is AssignmentGenerationMode {
  return value === "template_only" || value === "corpus_grounded" || value === "mixed";
}
