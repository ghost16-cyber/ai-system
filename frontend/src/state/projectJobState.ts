export type ProjectJobStatus =
  | "intake"
  | "needs_clarification"
  | "planned"
  | "patch_proposed"
  | "patch_approved"
  | "implementing"
  | "validating"
  | "blocked"
  | "completed"
  | "cancelled";

export interface ProjectJobAction {
  jobId: string;
  status: ProjectJobStatus;
  objective: string;
  deliverables: string[];
  constraints: string[];
  acceptanceCriteria: string[];
  relevantPaths: string[];
  missingInformation: string[];
  risks: string[];
  clarification?: { question?: string; answer?: string };
  plan: {
    findings: Array<{ claim: string; relativePath: string }>;
    files: string[];
    steps: string[];
    safetyImpact: string;
    assumptions: string[];
  };
  patchIds: string[];
  commandPlanIds: string[];
  validationPlan: Array<Record<string, unknown>>;
  validationResults: Array<Record<string, unknown>>;
  completionSummary?: Record<string, unknown>;
  revisionCount: number;
  maxRevisionCycles: number;
  technical: Record<string, unknown>;
}

const statuses = new Set<ProjectJobStatus>([
  "intake", "needs_clarification", "planned", "patch_proposed", "patch_approved",
  "implementing", "validating", "blocked", "completed", "cancelled",
]);

export function projectJobActionFromPayload(payload: unknown): ProjectJobAction | null {
  const action = asObject(payload);
  if (action.action_type !== "project_job") return null;
  const details = asObject(action.technical_details);
  const job = asObject(details.project_job);
  const jobId = readString(job.job_id);
  const rawStatus = readString(job.status) as ProjectJobStatus;
  if (!jobId || !statuses.has(rawStatus)) return null;
  const plan = asObject(job.implementation_plan);
  const paths = safePaths(job.relevant_paths);
  return {
    jobId,
    status: rawStatus,
    objective: readString(job.objective, "Project work"),
    deliverables: strings(job.deliverables),
    constraints: strings(job.constraints),
    acceptanceCriteria: strings(job.acceptance_criteria),
    relevantPaths: paths,
    missingInformation: strings(job.missing_information),
    risks: strings(job.risks),
    clarification: clarification(job.clarification),
    plan: {
      findings: objects(plan.current_state_findings).map((item) => ({
        claim: readString(item.claim),
        relativePath: safePath(readString(item.relative_path)),
      })).filter((item) => item.claim && item.relativePath),
      files: safePaths(plan.files_likely_involved),
      steps: strings(plan.steps),
      safetyImpact: readString(plan.safety_impact),
      assumptions: strings(plan.unresolved_assumptions),
    },
    patchIds: strings(job.patch_ids),
    commandPlanIds: strings(job.command_plan_ids),
    validationPlan: objects(job.validation_plan),
    validationResults: objects(job.validation_results),
    completionSummary: Object.keys(asObject(job.completion_summary)).length ? asObject(job.completion_summary) : undefined,
    revisionCount: readNumber(job.revision_count),
    maxRevisionCycles: readNumber(job.max_revision_cycles, 3),
    technical: job,
  };
}

export function mergeProjectJobAction(
  current: ProjectJobAction | undefined,
  incoming: ProjectJobAction | null,
): ProjectJobAction | undefined {
  if (!incoming) return current;
  if (!current || current.jobId === incoming.jobId) return incoming;
  return current;
}

function clarification(value: unknown) {
  const item = asObject(value);
  const question = readString(item.question);
  const answer = readString(item.answer);
  return question || answer ? { question: question || undefined, answer: answer || undefined } : undefined;
}

function safePaths(value: unknown): string[] {
  return strings(value).map(safePath).filter(Boolean).slice(0, 20);
}

function safePath(value: string): string {
  const normalized = value.replace(/\\/g, "/");
  if (!normalized || normalized.startsWith("/") || /^[A-Za-z]:\//.test(normalized) || normalized.includes("../")) return "";
  return normalized;
}

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string").slice(0, 30) : [];
}

function objects(value: unknown): Array<Record<string, unknown>> {
  return Array.isArray(value) ? value.map(asObject).filter((item) => Object.keys(item).length).slice(0, 30) : [];
}

function asObject(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function readString(value: unknown, fallback = ""): string { return typeof value === "string" ? value : fallback; }
function readNumber(value: unknown, fallback = 0): number { return typeof value === "number" && Number.isFinite(value) ? value : fallback; }
