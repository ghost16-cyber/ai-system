import { useCallback, useEffect, useState } from "react";
import { HttpAstraClient } from "../clients/astraClient";
import type {
  ExecutionProfile,
  FeatureConnection,
  OrchestratorJob,
  PolicyExplanation,
  RunHistoryItem,
  RuntimeContext,
  RuntimeEvidence,
  RuntimeResearchManifest,
  SpecialistRouteResult,
  SpecialistSignal,
  SlmCoordinatorSignal,
  TaskKind,
  ToolCall,
  WorkflowScenario,
  RuntimePlanValidation,
} from "../types/contracts";

export const BASE_URL =
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  (import.meta as any).env?.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";

export const apiClient = new HttpAstraClient(BASE_URL);

// ─── Raw backend types ────────────────────────────────────────────────────────

export interface HealthData {
  status: string;
  service: string;
  version: string;
  phase: string;
  database: string;
  timestamp: string;
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

export interface RawTool {
  name: string;
  description: string;
  read_only: boolean;
  execution: string;
}

// ─── Conversion helpers ───────────────────────────────────────────────────────

function formatAge(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const m = Math.floor(diff / 60_000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

function calcDuration(start: string, end: string): string {
  const diff = new Date(end).getTime() - new Date(start).getTime();
  const sec = Math.round(diff / 1_000);
  if (sec < 60) return `${sec}s`;
  return `${Math.round(sec / 60)}m`;
}

function mapJobStatus(raw: string): OrchestratorJob["status"] {
  if (raw === "succeeded") return "completed";
  if (raw === "cancelled") return "failed";
  return raw as OrchestratorJob["status"];
}

function jobTitle(job: RawJob): string {
  if (job.job_type === "analyze_project") return "Analyze project";
  if (job.job_type === "orchestrate_task") {
    const goal = (job.result as Record<string, unknown> | null)?.goal;
    if (goal) return String(goal).slice(0, 60);
    return "Orchestrate task";
  }
  return job.job_type.replace(/_/g, " ");
}

export function mapJobToOrchestratorJob(job: RawJob): OrchestratorJob {
  return {
    id: job.job_id,
    title: jobTitle(job),
    taskType: "Code repair",
    status: mapJobStatus(job.status),
    decision: "allow",
    duration:
      job.started_at && job.finished_at
        ? calcDuration(job.started_at, job.finished_at)
        : job.status === "running"
          ? "running…"
          : "—",
    updatedAt: formatAge(
      job.finished_at ?? job.started_at ?? job.created_at,
    ),
  };
}

export function mapHistoryToRunItem(item: RawHistoryItem): RunHistoryItem {
  return {
    id: item.analysis_id,
    title: item.filename ?? `Code analysis ${item.analysis_id.slice(0, 6)}`,
    type: "Code repair",
    status: item.issue_count === 0 ? "Passed" : "Downgraded",
    meta: `${item.line_count} lines / ${item.issue_count} issues`,
    time: formatAge(item.created_at),
    accent: item.issue_count === 0 ? "green" : "amber",
  };
}

export function mapToolToToolCall(tool: RawTool): ToolCall {
  return {
    id: tool.name,
    name: tool.name,
    state: "connected",
    status: "ready",
    detail: tool.description,
  };
}

export function deriveRuntimeEvidence(ctx: RuntimeContext): RuntimeEvidence[] {
  return [
    {
      id: "ev-vram",
      label: "GPU VRAM",
      value: `${ctx.machine.vramGb} GB`,
      detail: ctx.machine.cudaAvailable ? "CUDA available" : "CUDA unavailable",
      factIds: [],
    },
    {
      id: "ev-ram",
      label: "System RAM",
      value: `${ctx.machine.ramGb} GB`,
      detail: `${ctx.machine.logicalCores} logical cores`,
      factIds: [],
    },
    {
      id: "ev-storage",
      label: "Storage free",
      value: `${ctx.machine.storageFreeGb} GB`,
      detail: "Available workspace storage",
      factIds: [],
    },
    {
      id: "ev-cuda",
      label: "CUDA",
      value: ctx.machine.cudaAvailable ? "Available" : "Unavailable",
      detail: ctx.machine.gpu || "No GPU detected",
      factIds: [],
    },
  ];
}

export function deriveFeatureConnections(
  health: HealthData | null,
  tools: RawTool[],
): FeatureConnection[] {
  const live: FeatureConnection["state"] =
    health?.status === "ok" ? "connected" : "disabled";
  const liveDetail =
    health?.status === "ok"
      ? `Backend ${health.version} — ${health.phase}`
      : "Backend offline";

  return [
    { id: "dashboard", label: "Dashboard", state: live, detail: liveDetail },
    {
      id: "workspace",
      label: "Workspace",
      state: live,
      detail: live === "connected" ? "Orchestration active" : "Offline",
    },
    {
      id: "runtime",
      label: "Runtime",
      state: live,
      detail:
        live === "connected" ? "Live hardware context" : "Offline",
    },
    {
      id: "specialists",
      label: "Specialists",
      state: live,
      detail:
        live === "connected" ? "Specialist lifecycle active" : "Offline",
    },
    {
      id: "profiles",
      label: "Profiles",
      state: live,
      detail: live === "connected" ? "Built on demand" : "Offline",
    },
    {
      id: "traces",
      label: "Traces",
      state: live,
      detail: live === "connected" ? "Job trace available" : "Offline",
    },
    {
      id: "repository",
      label: "Repository",
      state: "disabled",
      detail: "No file system API",
    },
    {
      id: "patches",
      label: "Patches",
      state: live,
      detail:
        live === "connected" ? "From analysis results" : "Offline",
    },
    {
      id: "tests",
      label: "Tests",
      state: "disabled",
      detail: "No test runner API",
    },
    {
      id: "toolchain",
      label: "Toolchain",
      state: tools.length > 0 ? "connected" : live,
      detail: `${tools.length} tools registered`,
    },
  ];
}

// ─── Scenario builder for useAstraWorkflow ────────────────────────────────────

export function defaultPlanForTaskKind(
  taskKind: TaskKind,
): Record<string, unknown> {
  switch (taskKind) {
    case "Code repair":
      return { strategy: "code_repair", use_static_analysis: true };
    case "Local SLM":
      return { strategy: "local_inference", model_size_billion_params: 8 };
    case "RAG workflow":
      return { strategy: "rag_retrieval", use_embeddings: true };
    case "Model training":
      return { strategy: "pytorch_training", model_size_billion_params: 1 };
    case "Classical ML":
      return { strategy: "sklearn_training", use_gpu: false };
  }
}

export function fallbackValidation(
  taskKind: TaskKind,
): RuntimePlanValidation {
  const plan = defaultPlanForTaskKind(taskKind);
  return {
    decision: "allow",
    allowed: true,
    reason: "Validation service unavailable. Proceeding with default policy.",
    blockedSignals: [],
    requestedPlan: plan,
    recommendedPlan: plan,
  };
}

export function buildScenarioFromApiData({
  task,
  taskKind,
  context,
  validation,
  profile,
  routeResult,
}: {
  task: string;
  taskKind: TaskKind;
  context: RuntimeContext | null;
  validation: RuntimePlanValidation;
  profile: ExecutionProfile | null;
  routeResult: SpecialistRouteResult | null;
}): WorkflowScenario {
  const runtimeEvidence: RuntimeEvidence[] = context
    ? deriveRuntimeEvidence(context)
    : [];

  const policyExplanations: PolicyExplanation[] = context
    ? [
        context.policy.lowVramMode
          ? {
              id: "low_vram",
              title: "Low-VRAM mode",
              detail:
                "GPU memory is limited. Large models are restricted.",
              tone: "amber",
              factIds: [],
            }
          : {
              id: "vram_ok",
              title: "VRAM adequate",
              detail: "GPU memory allows standard model loading.",
              tone: "green",
              factIds: [],
            },
        {
          id: "cpu_fallback",
          title: "CPU fallback",
          detail: context.policy.cpuFallbackAllowed
            ? "CPU execution permitted if GPU is unavailable."
            : "CPU-only execution is restricted.",
          tone: context.policy.cpuFallbackAllowed ? "green" : "amber",
          factIds: [],
        },
        context.policy.preferQuantizedModels
          ? {
              id: "quantized",
              title: "Quantized models",
              detail: "Prefer quantized models to reduce VRAM usage.",
              tone: "green",
              factIds: [],
            }
          : null,
      ].filter(Boolean) as PolicyExplanation[]
    : [];

  const specialistSignals: SpecialistSignal[] = routeResult
    ? [
        {
          specialist: routeResult.recommended_specialist,
          label: routeResult.task_type,
          confidence: routeResult.confidence,
          reason: routeResult.fallback_required
            ? "Fallback routing (no promoted model available)"
            : "Routing via promoted specialist model",
          advisoryOnly: routeResult.advisory_only,
        },
      ]
    : [];

  const slmSignal: SlmCoordinatorSignal = {
    model: "qwen2.5-coder:1.5b",
    role: "coordinator",
    proposedAction:
      validation.decision === "block" ? "reject" : "final_response",
    reason: validation.reason,
    advisoryOnly: true,
  };

  const blocked = validation.decision === "block";

  return {
    id: `real-${taskKind}-${Date.now()}`,
    taskKind,
    title: task,
    recommendedPrompt: task,
    requestedPlan: validation.requestedPlan ?? {},
    validation,
    slmSignal,
    specialistSignals,
    activeProfile: profile,
    activeProfileId: profile?.id ?? null,
    runtimeEvidence,
    policyExplanations,
    traceDetails: {
      runtime: context
        ? `${context.machine.gpu || "CPU"} / ${context.machine.logicalCores} cores / ${context.machine.ramGb} GB RAM`
        : "Runtime context unavailable.",
      research: validation.reason,
      gate: validation.reason,
      profile: profile
        ? `${profile.name} (${profile.runtime} / ${profile.device})`
        : "No profile built.",
      authorization: blocked
        ? "Plan blocked by runtime policy."
        : "Plan authorized.",
      tools: blocked
        ? "No tools activated."
        : `Task queued via ${taskKind} workflow.`,
      response: blocked
        ? validation.reason
        : profile
          ? `Running on ${profile.runtime} (${profile.device.toUpperCase()}). Check Jobs view for progress.`
          : "Task dispatched to backend.",
    },
    patchVisible: false,
    testsVisible: false,
    finalMessage: blocked
      ? validation.reason
      : profile
        ? `Profile: ${profile.name} / ${profile.runtime} (${profile.device.toUpperCase()}). Check Jobs for live status.`
        : "Task dispatched. Check Jobs view for progress.",
  };
}

// ─── React hooks ─────────────────────────────────────────────────────────────

export function useRuntimeContext(task?: string) {
  const [data, setData] = useState<RuntimeContext | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    apiClient
      .getRuntimeContext(task)
      .then((d) => { if (!cancelled) setData(d); })
      .catch((e: unknown) => { if (!cancelled) setError(String(e)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [task]);

  return { data, loading, error };
}

export function useRuntimeResearchManifest() {
  const [data, setData] = useState<RuntimeResearchManifest | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    apiClient
      .getRuntimeResearchManifest()
      .then((d) => { if (!cancelled) setData(d); })
      .catch((e: unknown) => { if (!cancelled) setError(String(e)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  return { data, loading, error };
}

export function useHealth() {
  const [data, setData] = useState<HealthData | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(() => {
    fetch(`${BASE_URL}/health`)
      .then((r) => r.json() as Promise<HealthData>)
      .then(setData)
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { load(); }, [load]);
  return { data, loading, refresh: load };
}

export function useTools() {
  const [data, setData] = useState<RawTool[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetch(`${BASE_URL}/tools`)
      .then((r) => r.json() as Promise<{ items: RawTool[] }>)
      .then((d) => { if (!cancelled) setData(d.items ?? []); })
      .catch(() => { if (!cancelled) setData([]); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  return { data, loading };
}

export function useJobs(pollMs = 0) {
  const [data, setData] = useState<RawJob[]>([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(() => {
    fetch(`${BASE_URL}/jobs`)
      .then((r) => r.json() as Promise<{ items: RawJob[] }>)
      .then((d) => setData(d.items ?? []))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
    if (pollMs > 0) {
      const id = window.setInterval(load, pollMs);
      return () => window.clearInterval(id);
    }
  }, [load, pollMs]);

  return { data, loading, refresh: load };
}

export function useHistory(limit = 10) {
  const [data, setData] = useState<RawHistoryItem[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetch(`${BASE_URL}/history?limit=${limit}`)
      .then((r) => r.json() as Promise<{ items: RawHistoryItem[] }>)
      .then((d) => { if (!cancelled) setData(d.items ?? []); })
      .catch(() => { if (!cancelled) setData([]); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [limit]);

  return { data, loading };
}
