import type {
  CanonicalProjectActionDescriptor,
  CanonicalProjectActionRequest,
  CanonicalProjectEventSummary,
  CanonicalProjectResponse,
} from "../types/contracts";

export interface CanonicalProjectAction {
  projectRunId: string;
  conversationId: string;
  workspaceId: string;
  actorId: string;
  repositoryRootFingerprint: string;
  lifecycleState: string;
  stateVersion: number;
  terminal: boolean;
  pendingUserAction: string | null;
  blockedReason: string | null;
  approvalState: string;
  approvalFresh: boolean;
  manifestComplete: boolean;
  progress: Record<string, number>;
  verificationSummary: Record<string, number>;
  criterionStates: Record<string, Record<string, unknown>>;
  repairState: Record<string, unknown>;
  handoffEligible: boolean;
  execution: {
    attemptId: string | null;
    attemptType: string | null;
    attemptStatus: string | null;
    dispatchId: string | null;
    dispatchStatus: string | null;
    workerRequestId: string | null;
    workerStatus: string | null;
    cancellationId: string | null;
    cancellationStatus: string | null;
    failureClassification: string | null;
  };
  projection: { status: string | null; lag: number; failureClassification: string | null };
  coordinator: CanonicalProjectResponse["coordinator"];
  artifacts: CanonicalProjectResponse["artifacts"];
  nextPermittedActions: CanonicalProjectActionDescriptor[];
  response: CanonicalProjectResponse;
}

export interface CanonicalActionRetryIdentity {
  idempotencyKey: string;
  storageSlot: string | null;
}

export interface CanonicalActionRetryStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

export interface CanonicalManualEvidenceRetryRequest {
  idempotency_key: string;
  expected_state_version: number;
  verification_artifact_id: string;
  verification_artifact_hash: string;
}

const supportedActions = new Set([
  "approve_plan", "approve_patch", "approve_command", "approve_rollback",
  "cancel_project",
]);

export function canonicalProjectActionFromResponse(value: unknown): CanonicalProjectAction | null {
  if (!isObject(value) || value.schema_version !== "astra.project-api.project.v1") return null;
  const response = value as unknown as CanonicalProjectResponse;
  const project = isObject(response.project) ? response.project : null;
  if (!project || project.schema_version !== "astra.project-control.read-model.v1") return null;
  if (!text(project.project_run_id) || !text(project.conversation_id) || !text(project.workspace_id)
      || !text(project.actor_id) || !text(project.repository_root_fingerprint)
      || !Number.isInteger(project.state_version) || project.state_version < 1) return null;
  const artifacts = Array.isArray(response.artifacts) ? response.artifacts.filter((item) =>
    isObject(item) && item.schema_version === "astra.project-api.artifact-summary.v1"
      && Boolean(text(item.artifact_id)) && Boolean(text(item.content_hash)),
  ) : [];
  const nextPermittedActions = Array.isArray(response.next_permitted_actions)
    ? response.next_permitted_actions.filter((item) => actionMatchesProject(item, project, artifacts))
    : [];
  return {
    projectRunId: project.project_run_id,
    conversationId: project.conversation_id,
    workspaceId: project.workspace_id,
    actorId: project.actor_id,
    repositoryRootFingerprint: project.repository_root_fingerprint,
    lifecycleState: text(project.lifecycle_state, "blocked"),
    stateVersion: project.state_version,
    terminal: project.terminal === true,
    pendingUserAction: nullableText(project.pending_user_action),
    blockedReason: nullableText(project.blocked_reason),
    approvalState: text(project.approval_state, "not_approved"),
    approvalFresh: project.approval_fresh === true,
    manifestComplete: project.manifest_complete === true,
    progress: numericRecord(project.progress),
    verificationSummary: numericRecord(project.verification_summary),
    criterionStates: recordOfRecords(project.criterion_states),
    repairState: isObject(project.repair_state) ? project.repair_state : {},
    handoffEligible: project.handoff_eligible === true,
    execution: {
      attemptId: nullableText(project.active_execution_attempt_id),
      attemptType: nullableText(project.active_execution_attempt_type),
      attemptStatus: nullableText(project.active_execution_attempt_status),
      dispatchId: nullableText(project.execution_dispatch_id),
      dispatchStatus: nullableText(project.execution_dispatch_status),
      workerRequestId: nullableText(project.worker_request_id),
      workerStatus: nullableText(project.worker_request_status),
      cancellationId: nullableText(project.execution_cancellation_id),
      cancellationStatus: nullableText(project.execution_cancellation_status),
      failureClassification: nullableText(project.execution_failure_classification),
    },
    projection: {
      status: nullableText(project.projection_status),
      lag: typeof project.projection_lag === "number" ? Math.max(0, project.projection_lag) : 0,
      failureClassification: nullableText(project.projection_failure_classification),
    },
    coordinator: response.coordinator ?? null,
    artifacts,
    nextPermittedActions,
    response: { ...response, artifacts, next_permitted_actions: nextPermittedActions },
  };
}

export function mergeCanonicalProjectAction(
  current: CanonicalProjectAction | undefined,
  incoming: CanonicalProjectAction | null,
): CanonicalProjectAction | undefined {
  if (!incoming) return current;
  if (!current) return incoming;
  if (current.projectRunId !== incoming.projectRunId) return current;
  return incoming.stateVersion >= current.stateVersion ? incoming : current;
}

export function exactProjectMutationRequest(
  project: CanonicalProjectAction,
  action: CanonicalProjectActionDescriptor,
  idempotencyKey: string,
): CanonicalProjectActionRequest | null {
  if (!actionMatchesProject(action, project.response.project, project.artifacts) || !idempotencyKey) return null;
  return {
    schema_version: "astra.project-api.action.v1",
    conversation_id: project.conversationId,
    workspace_id: project.workspaceId,
    actor_id: project.actorId,
    repository_root_fingerprint: project.repositoryRootFingerprint,
    expected_state_version: action.expected_state_version,
    idempotency_key: idempotencyKey,
    plan_revision_id: action.plan_revision_id,
    scope_revision_id: action.scope_revision_id,
    manifest_hash: action.manifest_hash,
    artifact_id: action.artifact_id,
    artifact_type: action.artifact_type,
    artifact_hash: action.artifact_hash,
    artifact_binding_hash: action.artifact_binding_hash,
    payload: { ...action.payload },
  };
}

const CANONICAL_ACTION_RETRY_PREFIX = "astra:canonical-action-retry:v1:";
const CANONICAL_MANUAL_EVIDENCE_RETRY_PREFIX =
  "astra:canonical-manual-evidence-retry:v1:";

export function canonicalActionRetryIdentity(
  project: CanonicalProjectAction,
  action: CanonicalProjectActionDescriptor,
  storage: CanonicalActionRetryStorage,
  createIdempotencyKey: () => string,
): CanonicalActionRetryIdentity | null {
  const candidate = exactProjectMutationRequest(
    project,
    action,
    "pending-retry-identity",
  );
  if (!candidate) return null;
  const fingerprint = stableJson({
    ...candidate,
    idempotency_key: "",
  });
  const storageSlot = [
    CANONICAL_ACTION_RETRY_PREFIX,
    encodeURIComponent(project.projectRunId),
    ":",
    action.action,
    ":",
    action.expected_state_version,
    ":",
    encodeURIComponent(action.artifact_id ?? "none"),
  ].join("");
  return retryIdentityForFingerprint(
    fingerprint,
    storageSlot,
    storage,
    createIdempotencyKey,
  );
}

export function canonicalManualEvidenceRetryIdentity(
  projectRunId: string,
  criterionId: string,
  request: CanonicalManualEvidenceRetryRequest,
  storage: CanonicalActionRetryStorage,
  createIdempotencyKey: () => string,
): CanonicalActionRetryIdentity | null {
  if (!projectRunId || !criterionId
      || !Number.isInteger(request.expected_state_version)
      || request.expected_state_version < 1
      || !request.verification_artifact_id
      || !request.verification_artifact_hash) return null;
  const fingerprint = stableJson({
    ...request,
    idempotency_key: "",
  });
  const storageSlot = [
    CANONICAL_MANUAL_EVIDENCE_RETRY_PREFIX,
    encodeURIComponent(projectRunId),
    ":",
    encodeURIComponent(criterionId),
    ":",
    request.expected_state_version,
    ":",
    encodeURIComponent(request.verification_artifact_id),
  ].join("");
  return retryIdentityForFingerprint(
    fingerprint,
    storageSlot,
    storage,
    createIdempotencyKey,
  );
}

function retryIdentityForFingerprint(
  fingerprint: string,
  storageSlot: string,
  storage: CanonicalActionRetryStorage,
  createIdempotencyKey: () => string,
): CanonicalActionRetryIdentity | null {
  try {
    const stored = parseRetryRecord(storage.getItem(storageSlot));
    if (stored?.fingerprint === fingerprint) {
      return {
        idempotencyKey: stored.idempotencyKey,
        storageSlot,
      };
    }
    const idempotencyKey = validIdempotencyKey(createIdempotencyKey());
    if (!idempotencyKey) return null;
    storage.setItem(storageSlot, JSON.stringify({
      fingerprint,
      idempotency_key: idempotencyKey,
    }));
    return { idempotencyKey, storageSlot };
  } catch {
    const idempotencyKey = validIdempotencyKey(createIdempotencyKey());
    return idempotencyKey
      ? { idempotencyKey, storageSlot: null }
      : null;
  }
}

export function clearCanonicalActionRetryIdentity(
  identity: CanonicalActionRetryIdentity,
  storage: CanonicalActionRetryStorage,
): void {
  if (!identity.storageSlot) return;
  try {
    const stored = parseRetryRecord(storage.getItem(identity.storageSlot));
    if (stored?.idempotencyKey === identity.idempotencyKey) {
      storage.removeItem(identity.storageSlot);
    }
  } catch {
    // Browser storage is a retry aid, never lifecycle authority.
  }
}

export function shouldRemoveCanonicalProject(httpStatus: number, errorCode?: string): boolean {
  return httpStatus === 404 && errorCode === "project_not_found";
}

const HUMAN_GATED_PENDING_ACTION_PREFIXES = [
  "approve_", "review_", "submit_manual_evidence", "answer_clarification", "finalize_project",
];

/** True only while ProjectCoordinator/ProjectWorkers may still be
 * progressing this project in the background without further human input
 * -- never while the project is terminal, and never while it is waiting on
 * a human-gated pending action (nothing advances until that human acts,
 * regardless of how often this polls). */
export function shouldPollCanonicalProject(project: CanonicalProjectAction): boolean {
  if (project.terminal) return false;
  const pending = project.pendingUserAction;
  if (!pending) return true;
  return !HUMAN_GATED_PENDING_ACTION_PREFIXES.some((prefix) => pending.startsWith(prefix));
}

/** Stable chronological order for the project timeline, independent of
 * fetch/arrival order (pagination, retries, or an out-of-order poll
 * response must never scramble the displayed sequence). */
export function sortCanonicalProjectEvents(
  events: CanonicalProjectEventSummary[],
): CanonicalProjectEventSummary[] {
  return [...events].sort((a, b) => a.sequence - b.sequence);
}

export function isCompletedCanonicalProject(project: CanonicalProjectAction): boolean {
  return project.terminal && project.lifecycleState === "completed";
}

export function isCancelledCanonicalProject(project: CanonicalProjectAction): boolean {
  return project.terminal && project.lifecycleState === "cancelled";
}

function actionMatchesProject(
  action: unknown,
  project: CanonicalProjectResponse["project"],
  artifacts: CanonicalProjectResponse["artifacts"],
): action is CanonicalProjectActionDescriptor {
  if (!isObject(action) || action.schema_version !== "astra.project-api.action-descriptor.v1") return false;
  if (!supportedActions.has(text(action.action)) || action.requires_approval !== true) return false;
  if (action.expected_state_version !== project.state_version
      || action.plan_revision_id !== project.plan_revision_id
      || action.scope_revision_id !== project.scope_revision_id
      || action.manifest_hash !== project.manifest_hash) return false;
  if (action.action !== "cancel_project") {
    if (!text(action.artifact_id) || !text(action.artifact_type) || !text(action.artifact_hash) || !text(action.artifact_binding_hash)) return false;
    if (!artifacts.some((item) => item.artifact_id === action.artifact_id && item.artifact_type === action.artifact_type
      && item.content_hash === action.artifact_hash && item.binding_hash === action.artifact_binding_hash)) return false;
  }
  return true;
}

function isObject(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}
function text(value: unknown, fallback = ""): string { return typeof value === "string" ? value : fallback; }
function nullableText(value: unknown): string | null { return typeof value === "string" && value ? value : null; }
function numericRecord(value: unknown): Record<string, number> {
  return Object.fromEntries(Object.entries(isObject(value) ? value : {}).filter((entry): entry is [string, number] => typeof entry[1] === "number"));
}
function recordOfRecords(value: unknown): Record<string, Record<string, unknown>> {
  return Object.fromEntries(Object.entries(isObject(value) ? value : {}).filter((entry): entry is [string, Record<string, unknown>] => isObject(entry[1])));
}

function parseRetryRecord(value: string | null): {
  fingerprint: string;
  idempotencyKey: string;
} | null {
  if (!value) return null;
  try {
    const parsed = JSON.parse(value) as unknown;
    if (!isObject(parsed) || typeof parsed.fingerprint !== "string") return null;
    const idempotencyKey = validIdempotencyKey(parsed.idempotency_key);
    return idempotencyKey
      ? { fingerprint: parsed.fingerprint, idempotencyKey }
      : null;
  } catch {
    return null;
  }
}

function validIdempotencyKey(value: unknown): string | null {
  return typeof value === "string" && value.length >= 1 && value.length <= 200
    ? value
    : null;
}

function stableJson(value: unknown): string {
  if (Array.isArray(value)) {
    return `[${value.map((item) => stableJson(item)).join(",")}]`;
  }
  if (isObject(value)) {
    return `{${Object.keys(value).sort().map((key) =>
      `${JSON.stringify(key)}:${stableJson(value[key])}`,
    ).join(",")}}`;
  }
  return JSON.stringify(value);
}
