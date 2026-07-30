import type {
  HardwareCapabilitySummary,
  LocalAICapabilityReport,
  LocalAIErrorInfo,
  LocalAIFetchStatus,
  LocalAIModelConfiguration,
  OllamaCapabilitySnapshot,
  OverallStatus,
} from "../types/localAI";

/** The one Local AI model profile the rest of the system treats as "the
 * configured model" (see `LocalAIService`'s seeded `configured-local-model`
 * profile on the backend). Overall Status and Diagnostics describe this
 * profile specifically -- Installed Models still lists every profile.
 * Kept here rather than in `types/localAI.ts` so that module stays type-only
 * (a mixed value/type import would need a runtime resolution of that file,
 * which fails under the plain-Node test runner's strict ESM resolution). */
const CONFIGURED_MODEL_PROFILE_ID = "configured-local-model";

export interface LocalAIViewState {
  status: LocalAIFetchStatus;
  models: LocalAIModelConfiguration[];
  capabilities: LocalAICapabilityReport | null;
  error: LocalAIErrorInfo | null;
  refreshing: boolean;
  pendingModelIds: string[];
  diagnosticsOpen: boolean;
}

export const initialLocalAIState: LocalAIViewState = {
  status: "idle",
  models: [],
  capabilities: null,
  error: null,
  refreshing: false,
  pendingModelIds: [],
  diagnosticsOpen: false,
};

export type LocalAIAction =
  | { type: "load_start" }
  | { type: "load_success"; models: LocalAIModelConfiguration[]; capabilities: LocalAICapabilityReport }
  | { type: "load_error"; error: LocalAIErrorInfo }
  | { type: "refresh_start" }
  | { type: "refresh_success"; capabilities: LocalAICapabilityReport }
  | { type: "refresh_error"; error: LocalAIErrorInfo }
  | { type: "model_action_start"; modelProfileId: string }
  | { type: "model_action_success"; model: LocalAIModelConfiguration }
  | { type: "model_action_error"; modelProfileId: string; error: LocalAIErrorInfo }
  | { type: "toggle_diagnostics" }
  | { type: "dismiss_error" };

export function localAIReducer(state: LocalAIViewState, action: LocalAIAction): LocalAIViewState {
  switch (action.type) {
    case "load_start":
      return { ...state, status: "loading", error: null };
    case "load_success":
      return {
        ...state, status: "ready", models: action.models, capabilities: action.capabilities, error: null,
      };
    case "load_error":
      return { ...state, status: "error", error: action.error };
    case "refresh_start":
      return { ...state, refreshing: true, error: null };
    case "refresh_success":
      return { ...state, refreshing: false, capabilities: action.capabilities };
    case "refresh_error":
      return { ...state, refreshing: false, error: action.error };
    case "model_action_start":
      return { ...state, pendingModelIds: addPending(state.pendingModelIds, action.modelProfileId), error: null };
    case "model_action_success":
      return {
        ...state,
        pendingModelIds: removePending(state.pendingModelIds, action.model.model_profile_id),
        models: replaceModel(state.models, action.model),
        error: null,
      };
    case "model_action_error":
      return {
        ...state,
        pendingModelIds: removePending(state.pendingModelIds, action.modelProfileId),
        error: action.error,
      };
    case "toggle_diagnostics":
      return { ...state, diagnosticsOpen: !state.diagnosticsOpen };
    case "dismiss_error":
      return { ...state, error: null };
    default:
      return state;
  }
}

function addPending(ids: string[], id: string): string[] {
  return ids.includes(id) ? ids : [...ids, id];
}

function removePending(ids: string[], id: string): string[] {
  return ids.filter((item) => item !== id);
}

function replaceModel(
  models: LocalAIModelConfiguration[], updated: LocalAIModelConfiguration,
): LocalAIModelConfiguration[] {
  return models.map((model) => (model.model_profile_id === updated.model_profile_id ? updated : model));
}

export function findConfiguredModel(
  models: LocalAIModelConfiguration[],
): LocalAIModelConfiguration | undefined {
  return (
    models.find((model) => model.model_profile_id === CONFIGURED_MODEL_PROFILE_ID)
    ?? models.find((model) => model.enabled && model.provider_id !== "fake-deterministic")
  );
}

/** Every branch below relabels a value the backend already computed
 * (`ModelProfile.policy_status`) -- this never infers availability from
 * anything probed or guessed on the client. */
export function classifyOverallStatus(model: LocalAIModelConfiguration | undefined): OverallStatus {
  if (!model) return "unavailable";
  switch (model.policy_status) {
    case "ready":
      return "ready";
    case "installed_not_enabled":
    case "disabled_by_policy":
    case "intentionally_disabled":
      return "disabled";
    case "provider_unreachable":
      return "provider_unreachable";
    case "model_not_installed":
      return "model_missing";
    default:
      return "unavailable";
  }
}

export function overallStatusLabel(status: OverallStatus): string {
  switch (status) {
    case "ready":
      return "Ready";
    case "disabled":
      return "Disabled";
    case "provider_unreachable":
      return "Provider unreachable";
    case "model_missing":
      return "Model missing";
    case "unavailable":
      return "Unavailable";
  }
}

export function summarizeHardware(report: LocalAICapabilityReport | null): HardwareCapabilitySummary {
  const find = (id: string) => report?.capabilities.find((item) => item.capability_id === id);
  return {
    cpu: String(find("cpu")?.status ?? "unknown"),
    memory: String(find("memory")?.status ?? "unknown"),
    gpu: String(find("gpu")?.status ?? "unknown"),
    cuda: String(find("cuda")?.status ?? "unknown"),
    vram: String(find("vram")?.status ?? "unknown"),
    provider: String(find("ollama")?.status ?? "unknown"),
  };
}

export function ollamaCapability(report: LocalAICapabilityReport | null): OllamaCapabilitySnapshot | null {
  const found = report?.capabilities.find((item) => item.capability_id === "ollama");
  if (!found) return null;
  const strings = (value: unknown): string[] => (Array.isArray(value) ? value.map(String) : []);
  return {
    status: String(found.status),
    endpoint: String(found.endpoint ?? ""),
    configuredModels: strings(found.configured_models),
    installedModels: strings(found.installed_models),
    loadedModels: strings(found.loaded_models),
    providerReachable: Boolean(found.provider_reachable),
    configuredModelMissing: Boolean(found.configured_model_missing),
    reason: typeof found.reason === "string" ? found.reason : null,
  };
}

/** `model_not_locally_available` (see `classifyLocalAIError` below) is a
 * single backend error code covering several distinct causes -- an
 * unreachable provider, a missing model, a stale capability snapshot, or
 * another local-availability admission condition. This reads the capability
 * snapshot itself to show which one actually applies, entirely separately
 * from the generic mutation-error message (never merged into it). */
export function diagnoseLocalAvailability(report: LocalAICapabilityReport | null): string {
  const ollama = ollamaCapability(report);
  if (ollama && !ollama.providerReachable) {
    return "Ollama is not reachable.";
  }
  if (ollama && ollama.providerReachable && ollama.configuredModelMissing) {
    return "The configured model is not installed.";
  }
  return "The model is not currently available locally.";
}

function isHttpErrorShaped(error: unknown): error is { status: number; message: string } {
  return (
    typeof error === "object" && error !== null
    && "status" in error && typeof (error as { status: unknown }).status === "number"
    && "message" in error && typeof (error as { message: unknown }).message === "string"
  );
}

/** Classifies a thrown client error into the exact four categories this
 * page is specified to show, without ever silently retrying. Duck-types
 * the `AstraHttpError` shape (`status`/`message`) rather than importing the
 * class itself, since every real `AstraHttpError` the client throws has
 * this shape regardless. */
export function classifyLocalAIError(error: unknown): LocalAIErrorInfo {
  if (isHttpErrorShaped(error)) {
    if (error.status === 409) {
      const code = extractErrorCode(error.message);
      if (code === "stale_configuration_version") {
        return { kind: "stale_version", message: "Configuration changed. Reload configuration.", detail: code };
      }
      if (code === "model_not_locally_available") {
        return {
          kind: "model_unavailable",
          message: "The configured model is not currently available locally. Refresh capabilities and confirm the model is installed.",
          detail: code,
        };
      }
      return { kind: "unknown", message: backendErrorMessage(error.message), detail: code };
    }
    return { kind: "backend", message: backendErrorMessage(error.message) };
  }
  return { kind: "network", message: "Unable to contact Astra backend." };
}

function extractErrorCode(rawMessage: string): string | undefined {
  try {
    const parsed = JSON.parse(rawMessage) as { detail?: { code?: string } };
    return parsed.detail?.code;
  } catch {
    return undefined;
  }
}

/** Local, minimal counterpart to `state/errorMessage.ts::describeAstraError`
 * (that function is not imported here so this module stays a value-import-
 * free leaf, importable from both the Vite build and the plain-Node test
 * runner without an extension mismatch). Backend errors not already handled
 * above by an explicit `detail.code` are raw response text; this only
 * unwraps a readable `detail`/`detail.message` if present. */
function backendErrorMessage(rawMessage: string): string {
  const trimmed = rawMessage.trim();
  if (!trimmed.startsWith("{")) return rawMessage;
  try {
    const parsed = JSON.parse(trimmed) as { detail?: unknown };
    const detail = parsed.detail;
    if (typeof detail === "string" && detail.trim()) return detail;
    if (detail && typeof detail === "object") {
      const message = (detail as Record<string, unknown>).message;
      if (typeof message === "string" && message.trim()) return message;
    }
    return rawMessage;
  } catch {
    return rawMessage;
  }
}
