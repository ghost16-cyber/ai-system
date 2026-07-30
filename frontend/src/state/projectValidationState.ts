export type ProjectValidationStatus =
  | "created" | "preparing_workspace" | "baseline_captured" | "ready"
  | "running" | "execution_paused" | "budget_exceeded"
  | "evaluating_acceptance" | "inspecting_deliverables" | "running_regression"
  | "quality_review" | "remediation_required" | "awaiting_human_review"
  | "delivery_ready" | "delivery_rejected" | "recovering" | "cancelled" | "failed";

export interface ValidationCriterionResult {
  id: string;
  text: string;
  result: string;
  blocking: boolean;
  humanReviewRequired: boolean;
  explanation?: string;
  evidence: Array<{ id: string; type: string; summary: string }>;
}

export interface ValidationArtifact {
  id: string;
  deliverableId: string;
  name: string;
  type: string;
  exists: boolean;
  sizeBytes: number;
  humanReviewRequired: boolean;
  warning?: string;
}

export interface ProjectValidationAction {
  campaignId: string;
  conversationId: string;
  status: ProjectValidationStatus;
  stateVersion: number;
  objective: string;
  scopeRevisionId: string;
  scopeHash: string;
  deliveryJobId: string;
  workspace?: { name: string; isolated: boolean };
  baseline?: { fileCount: number; totalBytes: number; stale: boolean; restorable: boolean };
  activeRunId?: string;
  run?: {
    runId: string;
    runNumber: number;
    status: ProjectValidationStatus;
    stateVersion: number;
    criteria: ValidationCriterionResult[];
    artifacts: ValidationArtifact[];
    manifestComplete?: boolean;
    missingDeliverables: string[];
    regression?: { blocking: boolean; unexpectedChangeCount: number; regressedTests: string[]; summary: string };
    quality?: { score: number; minimum: number; uncertainty: number; blockers: string[]; decision: string; dimensions: Array<{ name: string; score: number; confidence: number; explanation: string }> };
    findings: Array<{ id: string; category: string; severity: string; summary: string; blocking: boolean; route: string }>;
    automatedDecision?: string;
    resultHash?: string;
    humanReview?: { action: string; notes: string; reviewerId: string };
    budgetUsage: Record<string, number>;
  };
  technical: Record<string, unknown>;
}

const statuses = new Set<ProjectValidationStatus>([
  "created", "preparing_workspace", "baseline_captured", "ready", "running",
  "execution_paused", "budget_exceeded", "evaluating_acceptance",
  "inspecting_deliverables", "running_regression", "quality_review",
  "remediation_required", "awaiting_human_review", "delivery_ready",
  "delivery_rejected", "recovering", "cancelled", "failed",
]);

export function projectValidationActionFromPayload(payload: unknown): ProjectValidationAction | null {
  const action = object(payload);
  if (action.action_type !== "project_validation") return null;
  const value = object(object(action.technical_details).project_validation);
  const campaignId = string(value.campaign_id);
  const status = string(value.state) as ProjectValidationStatus;
  if (!campaignId || !statuses.has(status)) return null;
  const scope = object(value.scope);
  const project = object(value.project);
  const workspace = object(value.workspace);
  const baseline = object(value.baseline);
  const runValue = object(value.run);
  const acceptance = object(runValue.acceptance_summary);
  const deliverables = object(runValue.deliverables);
  const regression = object(runValue.regression);
  const quality = object(runValue.quality);
  const runStatus = string(runValue.state) as ProjectValidationStatus;
  const run = string(runValue.run_id) && statuses.has(runStatus) ? {
    runId: string(runValue.run_id),
    runNumber: number(runValue.run_number, 1),
    status: runStatus,
    stateVersion: number(runValue.state_version, 1),
    criteria: objects(acceptance.items).map((item) => ({
      id: string(item.criterion_id), text: string(item.criterion_text), result: string(item.result),
      blocking: item.blocking === true, humanReviewRequired: item.human_review_required === true,
      explanation: string(item.failure_explanation) || undefined,
      evidence: objects(item.evidence).map((evidence) => ({ id: string(evidence.evidence_id), type: string(evidence.type), summary: string(evidence.summary) })),
    })).filter((item) => item.id),
    artifacts: objects(deliverables.artifacts).map((item) => ({
      id: string(item.artifact_id), deliverableId: string(item.deliverable_id), name: string(item.client_name),
      type: string(item.artifact_type), exists: item.exists === true, sizeBytes: number(item.size_bytes),
      humanReviewRequired: item.human_review_required === true, warning: string(item.warning) || undefined,
    })).filter((item) => item.id),
    manifestComplete: typeof deliverables.complete === "boolean" ? deliverables.complete : undefined,
    missingDeliverables: strings(deliverables.missing_deliverable_ids),
    regression: Object.keys(regression).length ? {
      blocking: regression.blocking === true, unexpectedChangeCount: number(regression.unexpected_change_count),
      regressedTests: strings(regression.regressed_tests), summary: string(regression.summary),
    } : undefined,
    quality: Object.keys(quality).length ? {
      score: number(quality.overall_score), minimum: number(quality.minimum_score), uncertainty: number(quality.uncertainty),
      blockers: strings(quality.blocking_findings), decision: string(quality.automated_decision),
      dimensions: objects(quality.dimensions).map((item) => ({ name: string(item.name), score: number(item.score), confidence: number(item.confidence), explanation: string(item.explanation) })),
    } : undefined,
    findings: objects(runValue.findings).map((item) => ({
      id: string(item.finding_id), category: string(item.category), severity: string(item.severity),
      summary: string(item.summary), blocking: item.blocking === true, route: string(item.recommended_route),
    })).filter((item) => item.id),
    automatedDecision: string(runValue.automated_decision) || undefined,
    resultHash: string(runValue.result_hash) || undefined,
    humanReview: Object.keys(object(runValue.human_review)).length ? {
      action: string(object(runValue.human_review).action), notes: string(object(runValue.human_review).notes), reviewerId: string(object(runValue.human_review).reviewer_id),
    } : undefined,
    budgetUsage: numericRecord(runValue.budget_usage),
  } : undefined;
  return {
    campaignId, conversationId: string(value.conversation_id), status,
    stateVersion: number(value.state_version, 1), objective: string(scope.objective),
    scopeRevisionId: string(scope.revision_id), scopeHash: string(scope.scope_hash),
    deliveryJobId: string(project.delivery_job_id),
    workspace: Object.keys(workspace).length ? { name: string(workspace.display_name), isolated: workspace.isolated === true } : undefined,
    baseline: Object.keys(baseline).length ? { fileCount: number(baseline.file_count), totalBytes: number(baseline.total_bytes), stale: baseline.stale === true, restorable: baseline.restorable === true } : undefined,
    activeRunId: string(value.active_run_id) || undefined, run, technical: value,
  };
}

export function exactValidationReviewRequest(action: ProjectValidationAction, reviewAction: string, notes = "") {
  if (action.status !== "awaiting_human_review" || !action.run?.resultHash) return null;
  return {
    conversation_id: action.conversationId,
    expected_state_version: action.stateVersion,
    expected_run_version: action.run.stateVersion,
    scope_revision_id: action.scopeRevisionId,
    scope_hash: action.scopeHash,
    validation_result_hash: action.run.resultHash,
    actor_id: "local-user",
    action: reviewAction,
    notes,
  };
}

export function mergeProjectValidationAction(current: ProjectValidationAction | undefined, incoming: ProjectValidationAction | null) {
  if (!incoming) return current;
  return !current || current.campaignId === incoming.campaignId ? incoming : current;
}

function object(value: unknown): Record<string, unknown> { return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {}; }
function objects(value: unknown): Array<Record<string, unknown>> { return Array.isArray(value) ? value.map(object).filter((item) => Object.keys(item).length) : []; }
function string(value: unknown): string { return typeof value === "string" ? value : ""; }
function number(value: unknown, fallback = 0): number { return typeof value === "number" && Number.isFinite(value) ? value : fallback; }
function strings(value: unknown): string[] { return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string").slice(0, 500) : []; }
function numericRecord(value: unknown): Record<string, number> {
  const result: Record<string, number> = {};
  for (const [key, entry] of Object.entries(object(value))) if (typeof entry === "number" && Number.isFinite(entry)) result[key] = entry;
  return result;
}
