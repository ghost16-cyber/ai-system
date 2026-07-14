export type ProjectDeliveryStatus =
  | "understanding_request" | "inspecting_project" | "clarification_required"
  | "task_specification_ready" | "task_specification_blocked" | "execution_plan_ready"
  | "awaiting_plan_approval" | "plan_approved" | "preparing_work_unit"
  | "patch_preview_ready" | "patch_applied_not_verified" | "awaiting_command_approval"
  | "verification_running" | "verification_failed" | "stage8_diagnosis"
  | "replanning_required" | "limit_reached" | "rollback_preview"
  | "partially_completed" | "awaiting_manual_verification" | "delivery_completed"
  | "blocked" | "cancelled";

export interface DeliveryCriterion {
  id: string;
  requirement: string;
  required: boolean;
  verificationMode: string;
  state: string;
  blockedReason?: string;
}

export interface DeliveryWorkUnit {
  id: string;
  title: string;
  objective: string;
  status: string;
  dependencies: string[];
  files: string[];
  criterionIds: string[];
}

export interface ProjectDeliveryAction {
  deliveryJobId: string;
  status: ProjectDeliveryStatus;
  objective: string;
  specificationHash: string;
  specificationSource: "deterministic" | "model-assisted" | "unknown";
  requirements: string[];
  exclusions: string[];
  assumptions: string[];
  deliverables: string[];
  criteria: DeliveryCriterion[];
  clarification?: { question: string; answer?: string };
  plan?: {
    hash: string;
    revision: number;
    approved: boolean;
    source: string;
    confidence: number;
    workUnits: DeliveryWorkUnit[];
  };
  activeWorkUnitId?: string;
  progress: {
    completedWorkUnits: number;
    totalWorkUnits: number;
    satisfiedRequiredCriteria: number;
    totalRequiredCriteria: number;
  };
  patchIds: string[];
  commandPlanIds: string[];
  scopeChanges: Array<{ reason: string; explanation: string; paths: string[] }>;
  repair?: Record<string, unknown>;
  rollbackCount: number;
  handoff?: {
    status: string;
    hash: string;
    changedFiles: string[];
    validations: string[];
    limitations: string[];
    manualChecks: string[];
    rollbackAvailable: boolean;
  };
  error?: string;
  technical: Record<string, unknown>;
}

const statuses = new Set<ProjectDeliveryStatus>([
  "understanding_request", "inspecting_project", "clarification_required",
  "task_specification_ready", "task_specification_blocked", "execution_plan_ready",
  "awaiting_plan_approval", "plan_approved", "preparing_work_unit",
  "patch_preview_ready", "patch_applied_not_verified", "awaiting_command_approval",
  "verification_running", "verification_failed", "stage8_diagnosis",
  "replanning_required", "limit_reached", "rollback_preview", "partially_completed",
  "awaiting_manual_verification", "delivery_completed", "blocked", "cancelled",
]);

export function projectDeliveryActionFromPayload(payload: unknown): ProjectDeliveryAction | null {
  const action = object(payload);
  if (action.action_type !== "project_delivery") return null;
  const details = object(action.technical_details);
  const delivery = object(details.project_delivery);
  const deliveryJobId = string(delivery.delivery_job_id);
  const status = string(delivery.status) as ProjectDeliveryStatus;
  if (!deliveryJobId || !statuses.has(status)) return null;
  const specification = object(delivery.specification);
  const plan = object(delivery.plan);
  const approval = object(delivery.plan_approval);
  const progress = object(details.progress);
  const criteria = objects(specification.acceptance_criteria).map((item) => ({
    id: string(item.criterion_id), requirement: string(item.requirement),
    required: item.required !== false, verificationMode: string(item.verification_mode),
    state: string(item.verification_state, "pending"),
    blockedReason: string(item.blocked_reason) || undefined,
  })).filter((item) => item.id && item.requirement);
  const latest = new Map<string, string>();
  for (const record of objects(delivery.verification_records)) {
    const id = string(record.criterion_id);
    if (id) latest.set(id, string(record.state, "pending"));
  }
  for (const criterion of criteria) criterion.state = latest.get(criterion.id) ?? criterion.state;
  const clarifications = objects(delivery.clarifications);
  const pending = clarifications.find((item) => item.status === "pending") ?? clarifications[clarifications.length - 1];
  const handoff = object(delivery.handoff);
  const workUnits = objects(plan.work_units).map((item) => ({
    id: string(item.work_unit_id), title: string(item.title, "Work unit"),
    objective: string(item.objective), status: string(item.status, "pending"),
    dependencies: strings(item.dependencies), files: safePaths(item.expected_files),
    criterionIds: strings(item.criterion_references),
  })).filter((item) => item.id);
  return {
    deliveryJobId, status,
    objective: string(specification.normalized_objective, string(delivery.original_user_request, "Project delivery")),
    specificationHash: string(specification.specification_hash),
    specificationSource: specification.specification_source === "deterministic" || specification.specification_source === "model-assisted"
      ? specification.specification_source : "unknown",
    requirements: strings(specification.in_scope_requirements),
    exclusions: strings(specification.explicit_exclusions), assumptions: strings(specification.assumptions),
    deliverables: strings(specification.requested_deliverables), criteria,
    clarification: pending && string(pending.question) ? { question: string(pending.question), answer: string(pending.answer) || undefined } : undefined,
    plan: Object.keys(plan).length ? {
      hash: string(plan.plan_hash), revision: number(plan.plan_revision, 1),
      approved: string(approval.plan_hash) === string(plan.plan_hash), source: string(plan.plan_source),
      confidence: number(plan.confidence), workUnits,
    } : undefined,
    activeWorkUnitId: string(delivery.active_work_unit_id) || undefined,
    progress: {
      completedWorkUnits: number(progress.completed_work_units, workUnits.filter((item) => item.status === "satisfied").length),
      totalWorkUnits: number(progress.total_work_units, workUnits.length),
      satisfiedRequiredCriteria: number(progress.satisfied_required_criteria, criteria.filter((item) => item.required && ["satisfied", "waived-by-user"].includes(item.state)).length),
      totalRequiredCriteria: number(progress.total_required_criteria, criteria.filter((item) => item.required).length),
    },
    patchIds: objects(delivery.patch_references).map((item) => string(item.patch_id)).filter(Boolean),
    commandPlanIds: objects(delivery.command_references).map((item) => string(item.plan_id)).filter(Boolean),
    scopeChanges: objects(delivery.scope_changes).map((item) => ({
      reason: string(item.reason_code), explanation: string(item.explanation), paths: safePaths(item.affected_paths),
    })),
    repair: Object.keys(object(delivery.stage8)).length ? object(delivery.stage8) : undefined,
    rollbackCount: objects(delivery.rollback_records).length,
    handoff: Object.keys(handoff).length ? {
      status: string(handoff.completion_status), hash: string(handoff.handoff_hash),
      changedFiles: safePaths(handoff.changed_files), validations: strings(handoff.validation_commands_and_outcomes),
      limitations: strings(handoff.known_limitations), manualChecks: strings(handoff.manual_checks_still_required),
      rollbackAvailable: handoff.rollback_available === true,
    } : undefined,
    error: string(object(delivery.last_error).message) || undefined,
    technical: delivery,
  };
}

export function exactPlanApprovalRequest(action: ProjectDeliveryAction, conversationId: string) {
  if (!action.plan?.hash || action.status !== "awaiting_plan_approval") return null;
  return { conversation_id: conversationId, immutable_hash: action.plan.hash };
}

export function mergeProjectDeliveryAction(current: ProjectDeliveryAction | undefined, incoming: ProjectDeliveryAction | null) {
  if (!incoming) return current;
  return !current || current.deliveryJobId === incoming.deliveryJobId ? incoming : current;
}

function object(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}
function objects(value: unknown): Array<Record<string, unknown>> { return Array.isArray(value) ? value.map(object).filter((item) => Object.keys(item).length) : []; }
function string(value: unknown, fallback = ""): string { return typeof value === "string" ? value : fallback; }
function number(value: unknown, fallback = 0): number { return typeof value === "number" && Number.isFinite(value) ? value : fallback; }
function strings(value: unknown): string[] { return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string").slice(0, 50) : []; }
function safePaths(value: unknown): string[] {
  return strings(value).map((path) => path.replace(/\\/g, "/")).filter((path) => path && !path.startsWith("/") && !/^[A-Za-z]:\//.test(path) && !path.includes("../"));
}
