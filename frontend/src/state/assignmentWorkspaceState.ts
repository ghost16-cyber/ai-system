import type {
  AssignmentCopilotResult,
  AssignmentGenerationMode,
  AssignmentWorkspaceWriteResult,
} from "../clients/astraClient";

export interface AssignmentWorkspaceTarget {
  assignmentNumber: number;
  assignmentTitle: string;
  workspacePath: string;
  generationMode: AssignmentGenerationMode;
  plannedFileCount: number;
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

function readPositiveInteger(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) && value > 0
    ? value
    : null;
}

function readString(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function isGenerationMode(value: unknown): value is AssignmentGenerationMode {
  return value === "template_only" || value === "corpus_grounded" || value === "mixed";
}
