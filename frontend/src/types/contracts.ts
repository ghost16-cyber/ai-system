export type ConnectionState = "connected" | "mock" | "disabled";
export type PlanDecision = "allow" | "downgrade" | "block";
export type JobStatus =
  | "queued"
  | "running"
  | "completed"
  | "blocked"
  | "failed";

export type AstraWorkflowStage =
  | "idle"
  | "planning"
  | "runtime_checked"
  | "plan_validated"
  | "profile_built"
  | "authorized"
  | "running_mock"
  | "completed"
  | "blocked"
  | "failed";

export type NavigationId =
  | "dashboard"
  | "workspace"
  | "runtime"
  | "specialists"
  | "profiles"
  | "traces"
  | "repository"
  | "patches"
  | "tests"
  | "settings";

export type TaskKind =
  | "Code repair"
  | "Local SLM"
  | "RAG workflow"
  | "Model training"
  | "Classical ML";

export interface FeatureConnection {
  id: NavigationId | "toolchain";
  label: string;
  state: ConnectionState;
  detail: string;
}

export interface RuntimeContext {
  machine: {
    cpu: string;
    logicalCores: number;
    gpu: string;
    cudaAvailable: boolean;
    vramGb: number;
    ramGb: number;
    storageFreeGb: number;
  };
  policy: {
    lowVramMode: boolean;
    preferQuantizedModels: boolean;
    avoidLargeModels: boolean;
    cpuFallbackAllowed: boolean;
    preferRagOverFinetuning: boolean;
  };
}

export interface RuntimePlanValidation {
  decision: PlanDecision;
  allowed: boolean;
  reason: string;
  blockedSignals: string[];
  requestedPlan?: Record<string, unknown>;
  recommendedPlan: Record<string, unknown>;
}

export interface RuntimeResearchFact {
  id: string;
  label: string;
  status: "confirmed" | "recommended" | "warning";
  summary: string;
  evidence: string;
}

export interface RuntimeEvidence {
  id: string;
  label: string;
  value: string;
  detail: string;
  factIds: string[];
}

export interface PolicyExplanation {
  id: string;
  title: string;
  detail: string;
  tone: "green" | "amber" | "red" | "blue";
  factIds: string[];
}

export interface SlmCoordinatorSignal {
  model: string;
  role: "coordinator";
  proposedAction: string;
  reason: string;
  advisoryOnly: boolean;
}

export interface SpecialistSignal {
  specialist: string;
  label: string;
  confidence: number;
  reason: string;
  advisoryOnly: boolean;
}

export interface RuntimeResearchManifest {
  manifestVersion: string;
  sourceFolder: string;
  hardwareBaseline: {
    gpu: string;
    vramGb: number;
    cudaAvailable: boolean;
    pytorchCudaAvailable: boolean;
    cpuThreads: number;
  };
  facts: RuntimeResearchFact[];
  policyDefaults: {
    lowVramMode: boolean;
    preferQuantizedModels: boolean;
    avoidLargeModels: boolean;
    preferRagOverFinetuning: boolean;
    cpuFallbackAllowed: boolean;
    maxRecommendedLocalModelBillionParams: number;
  };
  usageNote: string;
}

export interface WorkflowScenario {
  id: string;
  taskKind: TaskKind;
  title: string;
  recommendedPrompt: string;
  requestedPlan: Record<string, unknown>;
  validation: RuntimePlanValidation;
  slmSignal: SlmCoordinatorSignal;
  specialistSignals: SpecialistSignal[];
  activeProfile: ExecutionProfile | null;
  activeProfileId: string | null;
  runtimeEvidence: RuntimeEvidence[];
  policyExplanations: PolicyExplanation[];
  traceDetails: {
    runtime: string;
    research: string;
    gate: string;
    profile: string;
    authorization: string;
    tools: string;
    response: string;
  };
  patchVisible: boolean;
  testsVisible: boolean;
  finalMessage: string;
}

export interface CompactTraceResponse {
  jobId: string;
  status: JobStatus;
  orchestratorStatus: JobStatus | null;
  finalResponse: string | null;
  trace: TraceEvent[];
}

export interface ExecutionProfile {
  id: string;
  name: string;
  taskType: string;
  strategy: string;
  runtime: string;
  device: "cpu" | "cuda" | "hybrid";
  status: "safe" | "limited" | "blocked";
  settings: Array<{ label: string; value: string }>;
  safeguards: string[];
}

export interface OrchestratorJob {
  id: string;
  title: string;
  taskType: TaskKind;
  status: JobStatus;
  decision: PlanDecision;
  duration: string;
  updatedAt: string;
}

export interface TraceEvent {
  id: string;
  phase: string;
  title: string;
  detail: string;
  status: "passed" | "active" | "warning" | "blocked";
  elapsed: string;
}

export interface RepositoryNode {
  name: string;
  path: string;
  kind: "folder" | "python" | "markdown" | "json" | "config";
  state?: "modified" | "new" | "clean";
  children?: RepositoryNode[];
}

export interface PatchProposal {
  id: string;
  file: string;
  title: string;
  status: "review" | "approved" | "applied" | "blocked";
  risk: "low" | "medium" | "high";
  changedLines: number;
  oldCode: string;
  newCode: string;
  checks: string[];
}

export interface TestRunResult {
  id: string;
  command: string;
  status: "passed" | "failed" | "running";
  passed: number;
  failed: number;
  duration: string;
  suites: Array<{
    name: string;
    status: "passed" | "failed";
    detail: string;
  }>;
}

export interface ToolCall {
  id: string;
  name: string;
  state: ConnectionState;
  status: "ready" | "missing" | "disabled";
  detail: string;
}

export interface RunHistoryItem {
  id: string;
  title: string;
  type: TaskKind;
  status: "Passed" | "Downgraded" | "Blocked";
  meta: string;
  time: string;
  accent: "green" | "amber" | "red";
}

export interface AstraWorkflowState {
  runId: string | null;
  task: string;
  taskKind: TaskKind;
  scenarioId: string | null;
  stage: AstraWorkflowStage;
  decision: PlanDecision | null;
  validation: RuntimePlanValidation | null;
  slmSignal: SlmCoordinatorSignal | null;
  specialistSignals: SpecialistSignal[];
  activeProfile: ExecutionProfile | null;
  activeProfileId: string | null;
  runtimeEvidence: RuntimeEvidence[];
  policyExplanations: PolicyExplanation[];
  traceEvents: TraceEvent[];
  patchVisible: boolean;
  testsVisible: boolean;
  testsRunning: boolean;
  finalMessage: string | null;
  error: string | null;
}

export type SpecialistModelStatus = "candidate" | "promoted" | "rejected" | "deactivated";

export interface SpecialistDashboard {
  total_models: number;
  models_by_status: Record<string, number>;
  total_datasets: number;
  datasets_by_status: Record<string, number>;
  total_training_jobs: number;
  training_jobs_by_status: Record<string, number>;
  recent_audit_events: Array<Record<string, unknown>>;
  recent_traces: Array<Record<string, unknown>>;
  recent_trace_summary: Record<string, unknown>;
  fallback_status: Record<string, unknown>;
  read_only: boolean;
}

export interface SpecialistModel {
  model_id: string;
  specialist: string;
  lifecycle_status: SpecialistModelStatus;
  active: boolean;
  valid: boolean;
  created_at?: string;
  metadata?: Record<string, unknown>;
}

export interface SpecialistModelsResponse {
  model_dir: string;
  models: SpecialistModel[];
  count: number;
}

export interface SpecialistTracesResponse {
  traces: Array<Record<string, unknown>>;
  count: number;
}

export interface SpecialistBenchmark {
  total_examples: number;
  correct: number;
  overall_accuracy: number;
  accuracy_by_task_type: Record<string, { total_examples: number; correct: number; accuracy: number }>;
  failures: Array<Record<string, unknown>>;
}

export interface SpecialistRouteResult {
  task_type: string;
  recommended_specialist: string;
  confidence: number;
  promoted_model_available: boolean;
  model_id: string | null;
  fallback_required: boolean;
  safety_notes: string[];
  advisory_only: boolean;
  execution_allowed: boolean;
  slm_intent_used?: boolean;
  slm_intent_summary?: Record<string, unknown> | null;
}

export type AstraWorkflowAction =
  | {
      type: "submit";
      task: string;
      taskKind: TaskKind;
      runId: string;
      scenario: WorkflowScenario;
    }
  | { type: "advance" }
  | { type: "reset" }
  | { type: "fail"; error: string };

export interface CanonicalArtifactSummary {
  schema_version: "astra.project-api.artifact-summary.v1";
  artifact_id: string;
  artifact_type: string;
  revision_number: number | null;
  binding_hash: string;
  content_hash: string;
  created_at: string;
}

export interface CanonicalProjectReadModel {
  schema_version: "astra.project-control.read-model.v1";
  project_run_id: string;
  conversation_id: string;
  workspace_id: string;
  actor_id: string;
  repository_root_fingerprint: string;
  lifecycle_state: string;
  plan_revision_id: string | null;
  scope_revision_id: string | null;
  manifest_hash: string | null;
  manifest_complete: boolean;
  approval_state: string;
  approval_fresh: boolean;
  current_work_unit: string | null;
  progress: Record<string, number>;
  pending_user_action: string | null;
  verification_summary: Record<string, number>;
  criterion_states: Record<string, Record<string, unknown>>;
  repair_state: Record<string, unknown>;
  blocked_reason: string | null;
  handoff_eligible: boolean;
  state_version: number;
  terminal: boolean;
  active_execution_attempt_id: string | null;
  active_execution_attempt_type: string | null;
  active_execution_attempt_status: string | null;
  execution_dispatch_id: string | null;
  execution_dispatch_status: string | null;
  worker_request_id: string | null;
  worker_request_status: string | null;
  execution_failure_classification: string | null;
  execution_cancellation_id: string | null;
  execution_cancellation_status: string | null;
  execution_evidence_references: Record<string, unknown>;
  projection_status: string | null;
  projection_lag: number;
  projection_failure_classification: string | null;
  artifact_references: Record<string, string>;
  artifact_hashes: Record<string, string>;
  next_permitted_action: string | null;
}

export interface CanonicalProjectActionDescriptor {
  schema_version: "astra.project-api.action-descriptor.v1";
  action: "approve_plan" | "approve_patch" | "approve_command" | "approve_rollback" | "approve_manual_verification" | "cancel_project";
  label: string;
  requires_approval: true;
  expected_state_version: number;
  plan_revision_id: string | null;
  scope_revision_id: string | null;
  manifest_hash: string | null;
  artifact_id: string | null;
  artifact_type: string | null;
  artifact_hash: string | null;
  artifact_binding_hash: string | null;
  payload: Record<string, unknown>;
}

export interface CanonicalCoordinatorSummary {
  schema_version: "astra.project-api.coordinator-summary.v1";
  coordinator_intent_id: string;
  intent_type: string;
  status: string;
  attempt_count: number;
  failure_classification: string | null;
  updated_at: string;
}

export interface CanonicalProjectResponse {
  schema_version: "astra.project-api.project.v1";
  project: CanonicalProjectReadModel;
  artifacts: CanonicalArtifactSummary[];
  coordinator: CanonicalCoordinatorSummary | null;
  next_permitted_actions: CanonicalProjectActionDescriptor[];
}

export interface CanonicalProjectActionRequest {
  schema_version: "astra.project-api.action.v1";
  conversation_id: string;
  workspace_id: string;
  actor_id: string;
  repository_root_fingerprint: string;
  expected_state_version: number;
  idempotency_key: string;
  plan_revision_id: string | null;
  scope_revision_id: string | null;
  manifest_hash: string | null;
  artifact_id: string | null;
  artifact_type: string | null;
  artifact_hash: string | null;
  artifact_binding_hash: string | null;
  payload: Record<string, unknown>;
}

export interface CanonicalProjectCollection {
  schema_version: "astra.project-api.collection.v1";
  items: CanonicalProjectResponse[];
  count: number;
}

export interface CanonicalProjectCreateRequest {
  schema_version: "astra.project-api.create.v1";
  conversation_id: string;
  workspace_id: string;
  repository_root: string;
  repository_root_fingerprint: string;
  actor_id?: string;
  idempotency_key: string;
  folder_authority: {
    status: "completed";
    action_id: string;
    conversation_id: string;
    workspace_id: string;
    repository_root_fingerprint: string;
  };
  specification: Record<string, unknown>;
  manifest: Record<string, unknown>;
  plan: Record<string, unknown> | null;
}
