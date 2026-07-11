import type {
  CompactTraceResponse,
  ExecutionProfile,
  RuntimeContext,
  RuntimePlanValidation,
  RuntimeResearchManifest,
  SpecialistBenchmark,
  SpecialistDashboard,
  SpecialistModelsResponse,
  SpecialistRouteResult,
  SpecialistTracesResponse,
  TaskKind,
} from "../types/contracts";

export interface HealthData {
  status: string;
  service: string;
  version: string;
  phase: string;
  database: string;
  timestamp: string;
}

export interface RawTool {
  name: string;
  description: string;
  read_only: boolean;
  execution: string;
}

export interface RawJob {
  job_id: string;
  job_type: string;
  status: string;
  result: Record<string, unknown> | null;
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface RawHistoryItem {
  analysis_id: string;
  created_at: string;
  language: string;
  filename: string | null;
  code_length: number;
  line_count: number;
  issue_count: number;
  phase: string;
}

export interface SlmProfilesResponse {
  profiles: Array<Record<string, unknown>>;
  count: number;
  supported_backends: string[];
  default_profile_id: string;
}

export interface SelectedSlmResponse {
  selected_profile_id: string;
  profile: Record<string, unknown>;
  loaded: boolean;
  prompts_executed: boolean;
  advisory_only: boolean;
}

export interface RagStatusResponse {
  status: string;
  workspace_root: string;
  indexed_file_count: number;
  project_index?: RagProjectIndexStatus;
  project_index_exists?: boolean;
  project_index_file_count?: number;
  project_index_chunk_count?: number;
  project_index_created_at?: string | null;
  project_root?: string;
  source_roots: string[];
  safe_extensions: string[];
  exclusions: string[];
  advisory_only: boolean;
  tools_executed: boolean;
}

export interface RagProjectIndexStatus {
  exists: boolean;
  status: string;
  root: string;
  created_at: string | null;
  indexed_files: number;
  indexed_chunks: number;
  skipped_files?: number;
}

export interface RagProjectIndexBuildResponse {
  root: string;
  created_at: string;
  indexed_files: number;
  indexed_chunks: number;
  skipped_files: number;
}

export interface RagEvaluationCaseDetail {
  case_id: string;
  query: string;
  category: string;
  description: string;
  expected_paths: string[];
  expected_terms?: string[];
  returned_paths: string[];
  passed: boolean;
  score: number;
  missing_expected_paths: string[];
  expected_terms_found?: boolean;
  sources_returned?: number;
}

export interface RagEvaluationResult {
  status: string;
  index_exists: boolean;
  created_at?: string;
  total_cases: number;
  passed_cases: number;
  failed_cases: number;
  path_hit_rate: number;
  average_top_score: number;
  average_sources_returned: number;
  cases: RagEvaluationCaseDetail[];
  message?: string;
}

export interface RagEvaluationStatusResponse {
  status: string;
  index_exists: boolean;
  evaluation_case_count: number;
  evaluation_cases?: Array<Record<string, unknown>>;
  latest_evaluation: RagEvaluationResult | null;
  latest_evaluation_path?: string;
}

export interface SlmChatResponse {
  message: string;
  assistant_response: string;
  selected_profile?: Record<string, unknown>;
  source?: string;
  provider?: string;
  model?: string | null;
  used_real_slm?: boolean;
  fallback_reason?: string | null;
  latency_ms?: number | null;
  backend_available?: boolean;
  advisory_only?: boolean;
  tools_executed?: boolean;
  patches_applied?: boolean;
  runtime_authorized?: boolean;
  context_results?: Array<Record<string, unknown>>;
  citations?: Array<Record<string, unknown>>;
  trace_id?: string;
}

export interface ChatRunRequest {
  message: string;
  use_rag: boolean;
  safety_mode?: string;
  conversation_id?: string | null;
}

export interface ChatTraceEntry {
  phase: string;
  title: string;
  detail: string;
  status: string;
  data?: Record<string, unknown>;
}

export interface ChatRunResponse {
  run_id: string;
  conversation_id: string;
  user_message: string;
  assistant_response: string;
  selected_specialist: string;
  intent: string;
  confidence: number;
  rag_used: boolean;
  rag_skip_reason: string | null;
  rag_context_count: number;
  rag_sources?: Array<{
    path: string;
    start_line: number | null;
    end_line: number | null;
    score: number;
  }>;
  source_count?: number;
  source_paths?: string[];
  grounding_status?: "grounded" | "weak" | "none";
  runtime_decision: string;
  safety_decision: string;
  used_real_slm: boolean;
  slm_provider: string;
  slm_model: string | null;
  slm_fallback_reason: string | null;
  slm_latency_ms: number | null;
  memory_used: boolean;
  memory_summary: string | null;
  created_at: string;
  trace_summary: ChatTraceEntry[];
}

export type TrainingLabel =
  | "general"
  | "code"
  | "rag"
  | "runtime"
  | "safety"
  | "training"
  | "frontend"
  | "backend"
  | "debugging"
  | "testing"
  | "unknown";

export type TrainingLabelStatus =
  | "unlabeled"
  | "suggested"
  | "confirmed"
  | "corrected"
  | "rejected";

export type UsefulnessRating = "good" | "okay" | "bad";

export interface TrainingExample {
  id: string;
  created_at: string;
  updated_at?: string | null;
  source: "chat_run" | "manual" | "imported";
  chat_run_id?: string | null;
  user_message: string;
  assistant_response?: string | null;
  routed_task_type?: string | null;
  routed_specialist?: string | null;
  routing_confidence?: number | null;
  rag_used: boolean;
  rag_skip_reason?: string | null;
  grounding_status?: string | null;
  source_paths: string[];
  safety_status?: string | null;
  suggested_label?: TrainingLabel | null;
  corrected_label?: TrainingLabel | null;
  final_label?: TrainingLabel | null;
  label_status: TrainingLabelStatus;
  usefulness_rating?: UsefulnessRating | null;
  notes?: string | null;
}

export interface TrainingDatasetStatus {
  total_examples: number;
  labeled_count: number;
  unlabeled_count: number;
  label_distribution: Record<string, number>;
  label_status_distribution: Record<string, number>;
  storage_path: string;
  last_updated: string | null;
}

export interface TrainingExamplesResponse {
  items: TrainingExample[];
  count: number;
  total_matching: number;
  storage_path: string;
}

export interface TrainingLabelRequest {
  corrected_label?: TrainingLabel | null;
  label_status: TrainingLabelStatus;
  usefulness_rating?: UsefulnessRating | null;
  notes?: string | null;
}

export interface TrainingExportResponse {
  path: string;
  row_count: number;
  label_distribution: Record<string, number>;
  format: "jsonl" | "csv";
}

export interface AssignmentCopilotRequest {
  text?: string;
  path?: string;
  selected_assignment?: "all" | "1" | "2" | "3";
  workspace_path?: string;
  dataset_path?: string;
  project_metadata?: Record<string, unknown>;
}

export interface AssignmentCopilotResult {
  parsed_document_summary: Record<string, unknown>;
  extracted_assignment_sections: Array<Record<string, unknown>>;
  action_plan: Record<string, unknown>;
  recommended_starter_files: Array<Record<string, unknown>>;
  evidence_checklist: Record<string, unknown>;
  safe_next_commands: Array<Record<string, unknown>>;
  report_draft: Record<string, unknown>;
  report_skeleton?: Record<string, unknown> | null;
  task_breakdown?: Record<string, unknown> | null;
  marking_readiness: Array<Record<string, unknown>>;
  next_recommended_step: string;
  workspace_inspection?: Record<string, unknown> | null;
  dataset_profile?: Record<string, unknown> | null;
  workspace_build_plans?: Array<Record<string, unknown>>;
  runbooks?: Array<Record<string, unknown>>;
  code_blueprints?: Array<Record<string, unknown>>;
  analysis_plans?: Array<Record<string, unknown>>;
  dashboard_specs?: Array<Record<string, unknown>>;
  final_readiness?: Record<string, unknown> | null;
  tools_executed: boolean;
  files_written: boolean;
  training_performed: boolean;
}

export interface AssignmentReportExportRequest {
  text?: string;
  path?: string;
  brief?: Record<string, unknown>;
  assignment_number?: number;
  workspace_path?: string;
  report_folder?: string;
  overwrite?: boolean;
}

export interface AssignmentReportExportResult {
  output_directory: string;
  created_files: string[];
  skipped_files: string[];
  refused_files: string[];
  overwrite: boolean;
  warnings: string[];
}

export interface AssignmentCodeWriteResult {
  workspace_path: string;
  created_files: string[];
  skipped_files: string[];
  refused_files: string[];
  warnings: string[];
  next_manual_steps: string[];
  overwrite: boolean;
  commands_executed: boolean;
  credentials_written: boolean;
}

export interface AssignmentDatasetMapping {
  dataset_path?: string | null;
  timestamp_column: Record<string, unknown>;
  primary_numeric_indicator: Record<string, unknown>;
  secondary_numeric_fields: Array<Record<string, unknown>>;
  category_grouping_column: Record<string, unknown>;
  classification_threshold_idea: string;
  dashboard_filter_column: Record<string, unknown>;
  spark_aggregation_columns: string[];
  snowflake_table_names: string[];
  redis_key_patterns: string[];
  warnings: string[];
  placeholders_used: boolean;
}

export interface AssignmentManifestWriteResult {
  workspace_path: string;
  manifest_path: string;
  written: boolean;
  skipped: boolean;
  refused: boolean;
  warnings: string[];
  overwrite: boolean;
}

export type AssignmentExecutionDisplayState =
  | "pending"
  | "approved"
  | "running"
  | "completed"
  | "failed"
  | "expired";

export interface AssignmentExecutionSuggestion {
  action: string;
  target: string | null;
  executable: string;
  arguments: string[];
  command: string;
  working_directory: string;
  purpose: string;
  expected_result: string;
  risk_level: string;
  timeout_seconds: number;
  requires_approval: true;
  executed: false;
}

export interface AssignmentCommandRecord {
  plan_id: string;
  assignment_id: string;
  assignment_task: string;
  expected_result: string;
  action: string;
  target: string | null;
  executable: string;
  arguments: string[];
  command: string;
  working_directory: string;
  purpose: string;
  risk_level: string;
  timeout_seconds: number;
  workspace: string;
  status: string;
  display_state: AssignmentExecutionDisplayState;
  approval_expires_at: string | null;
  approved_artifacts: Array<{ path: string; sha256: string }>;
  created_at: string;
  approved_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  exit_code: number | null;
  stdout: string;
  stderr: string;
  log_available: boolean;
  safety_limitations: string[];
}

export interface AssignmentExecutionSummary {
  assignment_id: string;
  workspace: string;
  planned_commands: AssignmentCommandRecord[];
  approval_state: string;
  execution_state: string;
  assignment_completion_inferred: false;
  warnings: string[];
  limitations: string[];
}

export interface ChatStreamEvent {
  event:
    | "run_started"
    | "specialist_selected"
    | "rag_completed"
    | "safety_completed"
    | "response_delta"
    | "run_completed"
    | "run_failed"
    | string;
  data: Record<string, unknown>;
}

export interface RuntimePlanValidationRequest {
  task: string;
  taskKind: TaskKind;
  requestedPlan: Record<string, unknown>;
}

export interface ExecutionProfileRequest {
  task: string;
  taskKind: TaskKind;
  requestedPlan: Record<string, unknown>;
}

export interface SlmStatusResponse {
  enabled: boolean;
  base_url: string;
  configured_model: string | null;
  selected_profile_id: string;
  selected_model: string | null;
  provider: string;
  reachable: boolean;
  available_models: string[];
}

export interface IntelligenceDashboardResponse {
  components: Array<Record<string, unknown>>;
  policy: Record<string, unknown>;
  worker_roles: Array<Record<string, unknown>>;
  worker_status: Record<string, unknown>;
  model_evaluation_summary: Record<string, unknown>;
  decision_traces: Array<Record<string, unknown>>;
  auditability: Record<string, unknown>;
}

type JsonObject = Record<string, unknown>;

export interface AstraClient {
  getHealth(): Promise<HealthData>;
  getTools(): Promise<RawTool[]>;
  getJobs(limit?: number): Promise<RawJob[]>;
  getHistory(limit?: number): Promise<RawHistoryItem[]>;
  getRuntimeContext(task?: string): Promise<RuntimeContext>;
  getRuntimeResearchManifest(): Promise<RuntimeResearchManifest>;
  validateRuntimePlan(
    request: RuntimePlanValidationRequest,
  ): Promise<RuntimePlanValidation>;
  buildExecutionProfile(
    request: ExecutionProfileRequest,
  ): Promise<ExecutionProfile>;
  getSlmProfiles(): Promise<SlmProfilesResponse>;
  getSelectedSlm(): Promise<SelectedSlmResponse>;
  getSlmStatus(): Promise<SlmStatusResponse>;
  selectSlmProfile(profileId: string): Promise<Record<string, unknown>>;
  getRagStatus(): Promise<RagStatusResponse>;
  rebuildRagIndex(): Promise<RagProjectIndexBuildResponse>;
  getRagIndexStatus(): Promise<RagProjectIndexStatus>;
  getRagEvaluationStatus(): Promise<RagEvaluationStatusResponse>;
  runRagEvaluation(selectedCases?: string[]): Promise<RagEvaluationResult>;
  chatWithSlm(
    message: string,
    context?: Record<string, unknown>,
  ): Promise<SlmChatResponse>;
  chatWithContext(message: string, limit?: number): Promise<SlmChatResponse>;
  runChat(request: ChatRunRequest): Promise<ChatRunResponse>;
  streamChat(
    request: ChatRunRequest,
    onEvent: (event: ChatStreamEvent) => void,
  ): Promise<ChatRunResponse>;
  getChatRuns(limit?: number): Promise<ChatRunResponse[]>;
  getTrainingDatasetStatus(): Promise<TrainingDatasetStatus>;
  getTrainingExamples(limit?: number): Promise<TrainingExamplesResponse>;
  labelTrainingExample(exampleId: string, request: TrainingLabelRequest): Promise<{ updated: boolean; example: TrainingExample }>;
  exportTrainingDataset(format: "jsonl" | "csv"): Promise<TrainingExportResponse>;
  runAssignmentCopilot(request: AssignmentCopilotRequest): Promise<AssignmentCopilotResult>;
  exportAssignmentReport(request: AssignmentReportExportRequest): Promise<AssignmentReportExportResult>;
  writeAssignmentCode(request: Record<string, unknown>): Promise<AssignmentCodeWriteResult>;
  mapAssignmentDataset(request: Record<string, unknown>): Promise<AssignmentDatasetMapping>;
  writeAssignmentManifest(request: Record<string, unknown>): Promise<AssignmentManifestWriteResult>;
  getCompactTrace(jobId: string): Promise<CompactTraceResponse>;
  getSpecialistDashboard(): Promise<SpecialistDashboard>;
  getSpecialistModels(): Promise<SpecialistModelsResponse>;
  getSpecialistTraces(): Promise<SpecialistTracesResponse>;
  getSpecialistRouterBenchmark(): Promise<SpecialistBenchmark>;
  routeSpecialistTask(text: string, useSlmIntent?: boolean): Promise<SpecialistRouteResult>;
  runSpecialistModelAction(modelId: string, action: "promote" | "deactivate" | "reject" | "rollback"): Promise<Record<string, unknown>>;
  getSpecialistModelReport(modelId: string): Promise<Record<string, unknown>>;
  getSpecialistModelAudit(modelId: string): Promise<Record<string, unknown>>;
  getIntelligenceDashboard(): Promise<IntelligenceDashboardResponse>;
}

export class HttpAstraClient implements AstraClient {
  constructor(
    private readonly baseUrl: string =
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      (import.meta as any).env?.VITE_API_BASE_URL ?? "http://127.0.0.1:8000",
  ) {}

  async getHealth() {
    return this.getJson<HealthData>("/health");
  }

  async getTools() {
    const response = await this.getJson<{ items?: RawTool[] }>("/tools");
    return Array.isArray(response.items) ? response.items : [];
  }

  async getJobs(limit = 20) {
    const response = await this.getJson<{ items?: RawJob[] }>(
      `/jobs?limit=${encodeURIComponent(String(limit))}`,
    );
    return Array.isArray(response.items) ? response.items : [];
  }

  async getHistory(limit = 20) {
    const response = await this.getJson<{ items?: RawHistoryItem[] }>(
      `/history?limit=${encodeURIComponent(String(limit))}`,
    );
    return Array.isArray(response.items) ? response.items : [];
  }

  async getRuntimeContext(task?: string) {
    const suffix = task ? `?task=${encodeURIComponent(task)}` : "";
    return mapRuntimeContext(
      await this.getJson<JsonObject>(`/runtime/context${suffix}`),
    );
  }

  async getRuntimeResearchManifest() {
    return mapRuntimeResearchManifest(
      await this.getJson<JsonObject>("/runtime/research-manifest"),
    );
  }

  async validateRuntimePlan(request: RuntimePlanValidationRequest) {
    return mapRuntimePlanValidation(
      await this.postJson<JsonObject>("/runtime/validate-plan", {
        task: request.task,
        requested_plan: request.requestedPlan,
      }),
    );
  }

  async buildExecutionProfile(request: ExecutionProfileRequest) {
    return mapExecutionProfile(
      await this.postJson<JsonObject>("/runtime/execution-profile", {
        task: request.task,
        requested_plan: request.requestedPlan,
      }),
    );
  }

  async getSlmProfiles() {
    return this.getJson<SlmProfilesResponse>("/runtime/slm/profiles");
  }

  async getSelectedSlm() {
    return this.getJson<SelectedSlmResponse>("/runtime/slm/selected");
  }

  async getSlmStatus() {
    return this.getJson<SlmStatusResponse>("/runtime/slm/status");
  }

  async selectSlmProfile(profileId: string) {
    return this.postJson<Record<string, unknown>>("/runtime/slm/select", {
      profile_id: profileId,
    });
  }

  async getRagStatus() {
    return this.getJson<RagStatusResponse>("/rag/status");
  }

  async rebuildRagIndex() {
    return this.postJson<RagProjectIndexBuildResponse>("/rag/index", {});
  }

  async getRagIndexStatus() {
    return this.getJson<RagProjectIndexStatus>("/rag/index/status");
  }

  async getRagEvaluationStatus() {
    return this.getJson<RagEvaluationStatusResponse>("/rag/evaluation/status");
  }

  async runRagEvaluation(selectedCases: string[] = []) {
    return this.postJson<RagEvaluationResult>("/rag/evaluate", {
      selected_cases: selectedCases,
    });
  }

  async chatWithSlm(message: string, context: Record<string, unknown> = {}) {
    return this.postJson<SlmChatResponse>("/slm/chat", {
      message,
      context,
    });
  }

  async chatWithContext(message: string, limit = 4) {
    return this.postJson<SlmChatResponse>("/slm/chat-with-context", {
      message,
      limit,
    });
  }

  async runChat(request: ChatRunRequest) {
    return this.postJson<ChatRunResponse>("/chat/run", request);
  }

  async streamChat(
    request: ChatRunRequest,
    onEvent: (event: ChatStreamEvent) => void,
  ) {
    const response = await fetch(`${this.baseUrl}/chat/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
    });
    if (!response.ok) throw new Error(await response.text());
    if (!response.body) throw new Error("Streaming response body was unavailable.");

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let completedRun: ChatRunResponse | null = null;

    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done });
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";
      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed) continue;
        const event = JSON.parse(trimmed) as ChatStreamEvent;
        onEvent(event);
        if (event.event === "run_failed") {
          throw new Error(readString(event.data.error, "Streaming chat failed."));
        }
        if (event.event === "run_completed") {
          const run = asObject(event.data.run) as unknown as ChatRunResponse;
          completedRun = run;
        }
      }
      if (done) break;
    }

    if (!completedRun) throw new Error("Streaming chat ended before run completion.");
    return completedRun;
  }

  async getChatRuns(limit = 30) {
    const response = await this.getJson<{ items?: ChatRunResponse[] }>(
      `/chat/runs?limit=${encodeURIComponent(String(limit))}`,
    );
    return Array.isArray(response.items) ? response.items : [];
  }

  async getTrainingDatasetStatus() {
    return this.getJson<TrainingDatasetStatus>("/training/dataset/status");
  }

  async getTrainingExamples(limit = 8) {
    return this.getJson<TrainingExamplesResponse>(
      `/training/examples?limit=${encodeURIComponent(String(limit))}`,
    );
  }

  async labelTrainingExample(exampleId: string, request: TrainingLabelRequest) {
    return this.postJson<{ updated: boolean; example: TrainingExample }>(
      `/training/examples/${encodeURIComponent(exampleId)}/label`,
      request,
    );
  }

  async exportTrainingDataset(format: "jsonl" | "csv") {
    return this.postJson<TrainingExportResponse>("/training/export", { format });
  }

  async runAssignmentCopilot(request: AssignmentCopilotRequest) {
    return this.postJson<AssignmentCopilotResult>("/assignments/copilot/run", request);
  }

  async exportAssignmentReport(request: AssignmentReportExportRequest) {
    return this.postJson<AssignmentReportExportResult>("/assignments/report/export", request);
  }

  async writeAssignmentCode(request: Record<string, unknown>) {
    return this.postJson<AssignmentCodeWriteResult>("/assignments/code/write", request);
  }

  async mapAssignmentDataset(request: Record<string, unknown>) {
    return this.postJson<AssignmentDatasetMapping>("/assignments/dataset/map", request);
  }

  async writeAssignmentManifest(request: Record<string, unknown>) {
    return this.postJson<AssignmentManifestWriteResult>("/assignments/manifest/write", request);
  }

  async getAssignmentExecutionSuggestions(assignmentId: string, workspacePath: string) {
    return this.getJson<{ assignment_id: string; workspace: string; suggestions: AssignmentExecutionSuggestion[]; executed: false }>(
      `/assignments/${encodeURIComponent(assignmentId)}/execution/suggestions?workspace_path=${encodeURIComponent(workspacePath)}`,
    );
  }

  async getAssignmentExecutionSummary(assignmentId: string, workspacePath: string) {
    return this.getJson<AssignmentExecutionSummary>(
      `/assignments/${encodeURIComponent(assignmentId)}/execution?workspace_path=${encodeURIComponent(workspacePath)}`,
    );
  }

  async planAssignmentCommand(request: Record<string, unknown>) {
    return this.postJson<AssignmentCommandRecord>("/assignments/commands/plan", request);
  }

  async approveAssignmentCommand(planId: string, request: Record<string, unknown>) {
    return this.postJson<{ plan: AssignmentCommandRecord; approval_token: string }>(
      `/assignments/commands/${encodeURIComponent(planId)}/approve`, request,
    );
  }

  async executeAssignmentCommand(planId: string, request: Record<string, unknown>) {
    return this.postJson<AssignmentCommandRecord>(
      `/assignments/commands/${encodeURIComponent(planId)}/execute`, request,
    );
  }

  async getCompactTrace(jobId: string) {
    return this.getJson<CompactTraceResponse>(
      `/jobs/${encodeURIComponent(jobId)}/trace/compact`,
    );
  }

  async getSpecialistDashboard() {
    return this.getJson<SpecialistDashboard>("/specialists/dashboard");
  }

  async getSpecialistModels() {
    return this.getJson<SpecialistModelsResponse>("/specialists/models");
  }

  async getSpecialistTraces() {
    return this.getJson<SpecialistTracesResponse>("/specialists/traces");
  }

  async getSpecialistRouterBenchmark() {
    return this.getJson<SpecialistBenchmark>("/specialists/router/benchmark");
  }

  async routeSpecialistTask(text: string, useSlmIntent = false) {
    return this.postJson<SpecialistRouteResult>("/specialists/route", {
      text,
      use_slm_intent: useSlmIntent,
    });
  }

  async runSpecialistModelAction(modelId: string, action: "promote" | "deactivate" | "reject" | "rollback") {
    return this.postJson<Record<string, unknown>>(
      `/specialists/models/${encodeURIComponent(modelId)}/${action}`,
      {},
    );
  }

  async getSpecialistModelReport(modelId: string) {
    return this.getJson<Record<string, unknown>>(
      `/specialists/models/${encodeURIComponent(modelId)}/report`,
    );
  }

  async getSpecialistModelAudit(modelId: string) {
    return this.getJson<Record<string, unknown>>(
      `/specialists/models/${encodeURIComponent(modelId)}/audit`,
    );
  }

  async getIntelligenceDashboard() {
    return this.getJson<IntelligenceDashboardResponse>("/intelligence/dashboard");
  }

  private async getJson<T>(path: string): Promise<T> {
    const response = await fetch(`${this.baseUrl}${path}`);
    if (!response.ok) throw new Error(await response.text());
    return response.json() as Promise<T>;
  }

  private async postJson<T>(path: string, body: unknown): Promise<T> {
    const response = await fetch(`${this.baseUrl}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!response.ok) throw new Error(await response.text());
    return response.json() as Promise<T>;
  }
}

function mapRuntimeContext(raw: JsonObject): RuntimeContext {
  if ("machine" in raw && "policy" in raw) return raw as unknown as RuntimeContext;

  const hardware = asObject(raw.hardware);
  const gpu = asObject(hardware.gpu);
  const ram = asObject(hardware.ram);
  const storage = asObject(hardware.storage);
  const policy = asObject(raw.policy);

  return {
    machine: {
      cpu: readString(hardware.cpu_name),
      logicalCores: readNumber(hardware.cpu_count),
      gpu: readString(gpu.name),
      cudaAvailable: readBoolean(gpu.cuda_available),
      vramGb: mbToGb(readNumber(gpu.vram_total_mb)),
      ramGb: mbToGb(readNumber(ram.total_mb)),
      storageFreeGb: mbToGb(readNumber(storage.free_mb)),
    },
    policy: {
      lowVramMode: readBoolean(policy.low_vram_mode),
      preferQuantizedModels: readBoolean(policy.prefer_quantized_models),
      avoidLargeModels: readBoolean(policy.avoid_large_models),
      cpuFallbackAllowed: readBoolean(policy.cpu_fallback_allowed),
      preferRagOverFinetuning: readBoolean(policy.prefer_rag_over_finetuning),
    },
  };
}

function mapRuntimeResearchManifest(raw: JsonObject): RuntimeResearchManifest {
  if ("manifestVersion" in raw) return raw as unknown as RuntimeResearchManifest;

  const baseline = asObject(raw.hardware_baseline);
  const defaults = asObject(raw.policy_defaults);

  return {
    manifestVersion: readString(raw.manifest_version),
    sourceFolder: readString(raw.source_folder),
    hardwareBaseline: {
      gpu: readString(baseline.gpu),
      vramGb: readNumber(baseline.vram_gb),
      cudaAvailable: readBoolean(baseline.cuda_available),
      pytorchCudaAvailable: readBoolean(baseline.pytorch_cuda_available),
      cpuThreads: readNumber(baseline.cpu_threads),
    },
    facts: Array.isArray(raw.facts)
      ? (raw.facts as RuntimeResearchManifest["facts"])
      : [],
    policyDefaults: {
      lowVramMode: readBoolean(defaults.low_vram_mode),
      preferQuantizedModels: readBoolean(defaults.prefer_quantized_models),
      avoidLargeModels: readBoolean(defaults.avoid_large_models),
      preferRagOverFinetuning: readBoolean(defaults.prefer_rag_over_finetuning),
      cpuFallbackAllowed: readBoolean(defaults.cpu_fallback_allowed),
      maxRecommendedLocalModelBillionParams: readNumber(
        defaults.max_recommended_local_model_billion_params,
      ),
    },
    usageNote: readString(raw.usage_note),
  };
}

function mapRuntimePlanValidation(raw: JsonObject): RuntimePlanValidation {
  if ("recommendedPlan" in raw) return raw as unknown as RuntimePlanValidation;

  return {
    decision: readDecision(raw.decision),
    allowed: readBoolean(raw.allowed),
    reason: readString(raw.reason),
    blockedSignals: readStringArray(raw.blocked_signals),
    requestedPlan: asObject(raw.requested_plan),
    recommendedPlan: asObject(raw.recommended_plan),
  };
}

function mapExecutionProfile(raw: JsonObject): ExecutionProfile {
  if ("taskType" in raw && Array.isArray(raw.settings)) {
    return raw as unknown as ExecutionProfile;
  }

  const taskType = readString(raw.task_type, "Runtime task");
  const strategy = readString(raw.strategy, "runtime");
  const runtime = readString(raw.runtime, "local");
  const settings = asObject(raw.settings);

  return {
    id: [taskType, strategy, runtime].join(":"),
    name: `${taskType} profile`,
    taskType,
    strategy,
    runtime,
    device: readDevice(raw.device),
    status: readBoolean(settings.low_vram_mode) ? "limited" : "safe",
    settings: Object.entries(settings).map(([label, value]) => ({
      label: label.replace(/_/g, " "),
      value: String(value),
    })),
    safeguards: readStringArray(raw.safeguards),
  };
}

function asObject(value: unknown): JsonObject {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as JsonObject)
    : {};
}

function readString(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function readStringArray(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
}

function readNumber(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function readBoolean(value: unknown): boolean {
  return value === true;
}

function mbToGb(value: number): number {
  return Math.round((value / 1024) * 10) / 10;
}

function readDecision(value: unknown): RuntimePlanValidation["decision"] {
  return value === "downgrade" || value === "block" ? value : "allow";
}

function readDevice(value: unknown): ExecutionProfile["device"] {
  return value === "cuda" || value === "hybrid" ? value : "cpu";
}
