export type ProjectDeliveryStatus =
  | "understanding_request" | "inspecting_project" | "clarification_required"
  | "task_specification_ready" | "task_specification_blocked" | "execution_plan_ready"
  | "awaiting_plan_approval" | "plan_approved" | "preparing_work_unit"
  | "patch_preview_ready" | "patch_applied_not_verified" | "awaiting_command_approval"
  | "verification_running" | "verification_failed" | "stage8_diagnosis"
  | "replanning_required" | "limit_reached" | "rollback_preview"
  | "partially_completed" | "awaiting_manual_verification" | "delivery_completed"
  | "blocked" | "cancelled";

export type ProjectLifecycleState =
  | "specification_pending" | "clarification_required" | "manifest_required" | "planning"
  | "awaiting_plan_approval" | "ready_for_work" | "work_in_progress"
  | "awaiting_patch_approval" | "awaiting_command_approval" | "verification_pending"
  | "repair_required" | "scope_change_required" | "rollback_pending" | "blocked"
  | "cancelled" | "completed" | "handoff_ready" | "handed_off";

export interface DeliveryCriterion {
  id: string;
  requirement: string;
  required: boolean;
  verificationMode: string;
  state: string;
  blockedReason?: string;
  verifierOutcome?: "passed" | "failed" | "inconclusive" | "manual_required" | "manual_evidence_required" | "verification_passed" | "verification_failed" | "verification_stale" | "verification_invalidated" | "stale";
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
  projectRunId: string;
  status: ProjectDeliveryStatus;
  lifecycleState: ProjectLifecycleState;
  scopeRevisionId?: string;
  workspaceId?: string;
  actorId?: string;
  repositoryRootFingerprint?: string;
  manifestHash?: string;
  compatibilityActionBinding?: Record<string, unknown>;
  stateVersion: number;
  pendingUserAction?: string;
  handoffEligible: boolean;
  execution?: {
    attemptId?: string;
    attemptType?: string;
    attemptStatus?: string;
    dispatchId?: string;
    dispatchStatus?: string;
    workerRequestId?: string;
    workerStatus?: string;
    cancellationId?: string;
    cancellationStatus?: string;
    projectionStatus?: string;
    projectionLag: number;
    recoveryClassification?: string;
    failureClassification?: string;
    evidence: Record<string, unknown>;
  };
  coordinatorIntent?: {
    id: string;
    type: string;
    status: string;
    failureClassification?: string;
  };
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
    revisionId?: string;
    approvalFresh: boolean;
  };
  manifest: { complete: boolean; hash?: string; error?: string };
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
  const control = object(delivery.project_control);
  const compatibilityActionBinding = object(delivery.compatibility_action_binding);
  const hasControl = Object.keys(control).length > 0;
  const deliveryJobId = string(delivery.delivery_job_id);
  const status = string(delivery.status) as ProjectDeliveryStatus;
  if (!deliveryJobId || !statuses.has(status)) return null;
  const specification = object(delivery.specification);
  const plan = object(delivery.plan);
  const approval = object(delivery.plan_approval);
  const planRevision = object(delivery.plan_revision);
  const manifest = object(delivery.project_state_manifest);
  const progress = object(details.progress);
  const canonicalProgress = object(control.progress);
  const canonicalCriteria = object(control.criterion_states);
  const coordinatorIntents = objects(delivery.coordinator_intents);
  const coordinatorIntent = coordinatorIntents[coordinatorIntents.length - 1];
  const criteria: DeliveryCriterion[] = objects(specification.acceptance_criteria).map((item) => ({
    id: string(item.criterion_id), requirement: string(item.requirement),
    required: item.required !== false, verificationMode: string(item.verification_mode),
    state: string(item.verification_state, "pending"),
    blockedReason: string(item.blocked_reason) || undefined,
  })).filter((item) => item.id && item.requirement);
  const latest = new Map<string, string>();
  const verifierOutcomes = new Map<string, DeliveryCriterion["verifierOutcome"]>();
  const currentManifestHash = string(delivery.project_state_hash);
  const currentRevisionId = string(control.plan_revision_id, string(planRevision.plan_revision_id));
  for (const result of objects(delivery.verifier_results)) {
    const id = string(result.criterion_id);
    if (!id) continue;
    const fresh = string(result.input_manifest_hash) === currentManifestHash && string(result.plan_revision_id) === currentRevisionId;
    const outcome = fresh ? string(result.outcome) : "stale";
    if (["passed", "failed", "inconclusive", "manual_required", "manual_evidence_required", "verification_passed", "verification_failed", "verification_stale", "verification_invalidated", "stale"].includes(outcome)) {
      verifierOutcomes.set(id, outcome as DeliveryCriterion["verifierOutcome"]);
    }
  }
  for (const record of objects(delivery.verification_records)) {
    const id = string(record.criterion_id);
    if (id) latest.set(id, string(record.state, "pending"));
  }
  for (const criterion of criteria) {
    if (hasControl) {
      const canonical = object(canonicalCriteria[criterion.id]);
      const outcome = string(canonical.outcome, "pending");
      criterion.verifierOutcome = ["passed", "failed", "inconclusive", "manual_required", "manual_evidence_required", "verification_passed", "verification_failed", "verification_stale", "verification_invalidated", "stale"].includes(outcome)
        ? outcome as DeliveryCriterion["verifierOutcome"] : undefined;
      criterion.state = outcome === "passed" ? "satisfied" : outcome;
    } else {
      // Read-only compatibility for historical cards that predate Stage 1.
      criterion.state = latest.get(criterion.id) ?? criterion.state;
      criterion.verifierOutcome = verifierOutcomes.get(criterion.id);
      if (criterion.state === "satisfied" && criterion.verifierOutcome !== "passed") {
        criterion.state = "stale";
        criterion.verifierOutcome = "stale";
      }
    }
  }
  const clarifications = objects(delivery.clarifications);
  const pending = clarifications.find((item) => item.status === "pending") ?? clarifications[clarifications.length - 1];
  const handoff = object(delivery.handoff);
  const workUnits = objects(plan.work_units).map((item) => ({
    id: string(item.work_unit_id), title: string(item.title, "Work unit"),
    objective: string(item.objective), status: string(item.status, "pending"),
    dependencies: strings(item.dependencies), files: safePaths(item.expected_files),
    criterionIds: strings(item.criterion_references),
  })).filter((item) => item.id);
  const runtimeStates = new Map(objects(delivery.work_unit_execution_states).map((item) => [string(item.work_unit_id), string(item.status)]));
  for (const unit of workUnits) {
    const status = runtimeStates.get(unit.id);
    if (status) unit.status = status;
  }
  return {
    deliveryJobId,
    projectRunId: string(control.project_run_id, deliveryJobId),
    status,
    lifecycleState: string(control.lifecycle_state, legacyLifecycle(status)) as ProjectLifecycleState,
    scopeRevisionId: string(control.scope_revision_id) || undefined,
    workspaceId: string(control.workspace_id) || undefined,
    actorId: string(control.actor_id) || undefined,
    repositoryRootFingerprint: string(control.repository_root_fingerprint) || undefined,
    manifestHash: string(control.manifest_hash) || undefined,
    compatibilityActionBinding: Object.keys(compatibilityActionBinding).length
      ? compatibilityActionBinding : undefined,
    stateVersion: number(control.state_version, number(delivery.state_version, 1)),
    pendingUserAction: string(control.pending_user_action) || undefined,
    handoffEligible: control.handoff_eligible === true,
    execution: string(control.active_execution_attempt_id) || string(control.execution_dispatch_id)
      ? {
        attemptId: string(control.active_execution_attempt_id) || undefined,
        attemptType: string(control.active_execution_attempt_type) || undefined,
        attemptStatus: string(control.active_execution_attempt_status) || undefined,
        dispatchId: string(control.execution_dispatch_id) || undefined,
        dispatchStatus: string(control.execution_dispatch_status) || undefined,
        workerRequestId: string(control.worker_request_id) || undefined,
        workerStatus: string(control.worker_request_status) || undefined,
        cancellationId: string(control.execution_cancellation_id) || undefined,
        cancellationStatus: string(control.execution_cancellation_status) || undefined,
        projectionStatus: string(control.projection_status) || undefined,
        projectionLag: number(control.projection_lag),
        recoveryClassification: string(control.projection_failure_classification) || undefined,
        failureClassification: string(control.execution_failure_classification) || undefined,
        evidence: object(control.execution_evidence_references),
      } : undefined,
    coordinatorIntent: coordinatorIntent && string(coordinatorIntent.coordinator_intent_id)
      ? {
        id: string(coordinatorIntent.coordinator_intent_id),
        type: string(coordinatorIntent.intent_type),
        status: string(coordinatorIntent.status),
        failureClassification: string(coordinatorIntent.failure_classification) || undefined,
      } : undefined,
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
      approved: hasControl ? control.approval_fresh === true : string(approval.plan_revision_id) === currentRevisionId
        && string(approval.plan_content_hash) === string(planRevision.content_hash), source: string(plan.plan_source),
      confidence: number(plan.confidence), workUnits,
      revisionId: currentRevisionId || undefined,
      approvalFresh: hasControl ? control.approval_fresh === true : string(approval.plan_revision_id) === currentRevisionId
        && string(approval.plan_content_hash) === string(planRevision.content_hash),
    } : undefined,
    manifest: {
      complete: hasControl ? control.manifest_complete === true : manifest.complete === true,
      hash: string(control.manifest_hash, string(manifest.manifest_hash)) || undefined,
      error: manifest.complete === false ? strings(manifest.incomplete_reasons).join("; ") || "Project manifest is incomplete." : undefined,
    },
    activeWorkUnitId: string(delivery.active_work_unit_id) || undefined,
    progress: {
      completedWorkUnits: number(canonicalProgress.completed_work_units, number(progress.completed_work_units, workUnits.filter((item) => item.status === "satisfied").length)),
      totalWorkUnits: number(canonicalProgress.total_work_units, number(progress.total_work_units, workUnits.length)),
      satisfiedRequiredCriteria: hasControl
        ? number(object(control.verification_summary).passed)
        : criteria.filter((item) => item.required && (item.state === "waived-by-user" || (item.state === "satisfied" && item.verifierOutcome === "passed"))).length,
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

function legacyLifecycle(status: ProjectDeliveryStatus): ProjectLifecycleState {
  const values: Partial<Record<ProjectDeliveryStatus, ProjectLifecycleState>> = {
    clarification_required: "clarification_required", awaiting_plan_approval: "awaiting_plan_approval",
    plan_approved: "ready_for_work", preparing_work_unit: "work_in_progress",
    patch_preview_ready: "awaiting_patch_approval", patch_applied_not_verified: "verification_pending",
    awaiting_command_approval: "awaiting_command_approval", verification_running: "verification_pending",
    verification_failed: "repair_required", stage8_diagnosis: "repair_required",
    replanning_required: "scope_change_required", rollback_preview: "rollback_pending",
    blocked: "blocked", cancelled: "cancelled", delivery_completed: "completed",
  };
  return values[status] ?? "planning";
}
