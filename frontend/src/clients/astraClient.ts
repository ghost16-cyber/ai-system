import {
  executionProfiles,
  runtimeContext,
  runtimeResearchManifest,
  scenarioForTask,
  traceEvents,
} from "../data/mockData";
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

export interface AstraClient {
  getRuntimeContext(task?: string): Promise<RuntimeContext>;
  getRuntimeResearchManifest(): Promise<RuntimeResearchManifest>;
  validateRuntimePlan(
    request: RuntimePlanValidationRequest,
  ): Promise<RuntimePlanValidation>;
  buildExecutionProfile(
    request: ExecutionProfileRequest,
  ): Promise<ExecutionProfile>;
  getCompactTrace(jobId: string): Promise<CompactTraceResponse>;
  getSpecialistDashboard(): Promise<SpecialistDashboard>;
  getSpecialistModels(): Promise<SpecialistModelsResponse>;
  getSpecialistTraces(): Promise<SpecialistTracesResponse>;
  getSpecialistRouterBenchmark(): Promise<SpecialistBenchmark>;
  routeSpecialistTask(text: string, useSlmIntent?: boolean): Promise<SpecialistRouteResult>;
  runSpecialistModelAction(modelId: string, action: "promote" | "deactivate" | "reject" | "rollback"): Promise<Record<string, unknown>>;
  getSpecialistModelReport(modelId: string): Promise<Record<string, unknown>>;
  getSpecialistModelAudit(modelId: string): Promise<Record<string, unknown>>;
}

export class MockAstraClient implements AstraClient {
  async getRuntimeContext() {
    return runtimeContext;
  }

  async getRuntimeResearchManifest() {
    return runtimeResearchManifest;
  }

  async validateRuntimePlan(request: RuntimePlanValidationRequest) {
    return scenarioForTask(request.taskKind, request.task).validation;
  }

  async buildExecutionProfile(request: ExecutionProfileRequest) {
    const scenario = scenarioForTask(request.taskKind, request.task);
    const profile = executionProfiles.find(
      (item) => item.id === scenario.activeProfileId,
    );
    if (!profile) {
      throw new Error("Blocked plans do not produce execution profiles.");
    }
    return profile;
  }

  async getCompactTrace(jobId: string): Promise<CompactTraceResponse> {
    return {
      jobId,
      status: "completed",
      orchestratorStatus: "completed",
      finalResponse: "Mock compact trace loaded from frontend fixtures.",
      trace: traceEvents,
    };
  }

  async getSpecialistDashboard(): Promise<SpecialistDashboard> {
    return mockSpecialistDashboard;
  }

  async getSpecialistModels(): Promise<SpecialistModelsResponse> {
    return mockSpecialistModels;
  }

  async getSpecialistTraces(): Promise<SpecialistTracesResponse> {
    return { traces: mockSpecialistDashboard.recent_traces, count: mockSpecialistDashboard.recent_traces.length };
  }

  async getSpecialistRouterBenchmark(): Promise<SpecialistBenchmark> {
    return mockSpecialistBenchmark;
  }

  async routeSpecialistTask(text: string): Promise<SpecialistRouteResult> {
    const lowered = text.toLowerCase();
    const runtime = lowered.includes("cuda") || lowered.includes("gpu") || lowered.includes("runtime");
    const safety = lowered.includes("secret") || lowered.includes("token") || lowered.includes("security");
    return {
      task_type: runtime ? "runtime" : safety ? "safety" : "general",
      recommended_specialist: runtime ? "runtime_specialist" : safety ? "safety_specialist" : "general_specialist",
      confidence: runtime || safety ? 0.75 : 0.35,
      promoted_model_available: false,
      model_id: null,
      fallback_required: true,
      safety_notes: [
        "Recommendation only.",
        "No tools are executed.",
        "No patch or runtime action is authorized by this router.",
      ],
      advisory_only: true,
      execution_allowed: false,
    };
  }

  async runSpecialistModelAction(): Promise<Record<string, unknown>> {
    throw new Error("Lifecycle actions require a connected backend.");
  }

  async getSpecialistModelReport(modelId: string) {
    return { model_id: modelId, mock: true, metrics: { accuracy: 0.92 } };
  }

  async getSpecialistModelAudit(modelId: string) {
    return { model_id: modelId, events: mockSpecialistDashboard.recent_audit_events };
  }
}

export class HttpAstraClient implements AstraClient {
  constructor(private readonly baseUrl = "") {}

  async getRuntimeContext(task?: string) {
    const suffix = task ? `?task=${encodeURIComponent(task)}` : "";
    return this.getJson<RuntimeContext>(`/runtime/context${suffix}`);
  }

  async getRuntimeResearchManifest() {
    return this.getJson<RuntimeResearchManifest>("/runtime/research-manifest");
  }

  async validateRuntimePlan(request: RuntimePlanValidationRequest) {
    return this.postJson<RuntimePlanValidation>("/runtime/validate-plan", {
      task: request.task,
      requested_plan: request.requestedPlan,
    });
  }

  async buildExecutionProfile(request: ExecutionProfileRequest) {
    return this.postJson<ExecutionProfile>("/runtime/execution-profile", {
      task: request.task,
      requested_plan: request.requestedPlan,
    });
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

const mockSpecialistDashboard: SpecialistDashboard = {
  total_models: 3,
  models_by_status: { candidate: 1, promoted: 1, rejected: 0, deactivated: 1 },
  total_datasets: 2,
  datasets_by_status: { uploaded: 0, validated: 1, approved: 1, rejected: 0, archived: 0 },
  total_training_jobs: 4,
  training_jobs_by_status: { queued: 0, running: 0, completed: 3, failed: 0, rejected: 1 },
  recent_audit_events: [
    { timestamp: "mock", action: "model_promoted", model_id: "intent-001", specialist: "intent_classifier" },
  ],
  recent_traces: [
    { trace_id: "trace-mock", timestamp: "mock", recommended_specialist: "runtime_specialist", decision_source: "router", fallback_used: true },
  ],
  recent_trace_summary: { total_recent_traces: 1, fallback_used_count: 1 },
  fallback_status: { rule_based_fallback_available: true, promoted_model_count: 1, fallback_required: false },
  read_only: true,
};

const mockSpecialistModels: SpecialistModelsResponse = {
  model_dir: "mock",
  count: 3,
  models: [
    {
      model_id: "intent-001",
      specialist: "intent_classifier",
      lifecycle_status: "promoted",
      active: true,
      valid: true,
      metadata: { created_at: "mock", metrics: { accuracy: 0.94, precision: 0.93, recall: 0.92, f1_score: 0.92 } },
    },
    {
      model_id: "runtime-002",
      specialist: "runtime_specialist",
      lifecycle_status: "candidate",
      active: false,
      valid: true,
      metadata: { created_at: "mock", metrics: { accuracy: 0.88, precision: 0.86, recall: 0.87, f1_score: 0.86 } },
    },
    {
      model_id: "safety-000",
      specialist: "safety_specialist",
      lifecycle_status: "deactivated",
      active: false,
      valid: true,
      metadata: { created_at: "mock", metrics: { accuracy: 0.9 } },
    },
  ],
};

const mockSpecialistBenchmark: SpecialistBenchmark = {
  total_examples: 7,
  correct: 7,
  overall_accuracy: 1,
  accuracy_by_task_type: {
    runtime: { total_examples: 1, correct: 1, accuracy: 1 },
    safety: { total_examples: 1, correct: 1, accuracy: 1 },
    rag: { total_examples: 1, correct: 1, accuracy: 1 },
  },
  failures: [],
};
