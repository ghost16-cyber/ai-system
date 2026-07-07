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
