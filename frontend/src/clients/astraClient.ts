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

type JsonObject = Record<string, unknown>;

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

export class HttpAstraClient implements AstraClient {
  constructor(
    private readonly baseUrl: string =
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      (import.meta as any).env?.VITE_API_BASE_URL ?? "http://127.0.0.1:8000",
  ) {}

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
