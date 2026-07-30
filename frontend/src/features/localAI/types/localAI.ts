import type {
  LocalAICapabilityReport,
  LocalAIModelConfiguration,
} from "../../../clients/astraClient";

export type { LocalAICapabilityReport, LocalAIModelConfiguration };

/** The five states this page is allowed to display, per spec. Every value
 * here is a direct, deterministic relabeling of a backend-computed field
 * (`ModelProfile.policy_status`) -- never a locally inferred judgment. */
export type OverallStatus =
  | "ready"
  | "unavailable"
  | "provider_unreachable"
  | "model_missing"
  | "disabled";

export type LocalAIFetchStatus = "idle" | "loading" | "ready" | "error";

export type LocalAIErrorKind =
  | "stale_version"
  | "model_unavailable"
  | "network"
  | "backend"
  | "unknown";

export interface LocalAIErrorInfo {
  kind: LocalAIErrorKind;
  message: string;
  detail?: string;
}

export interface OllamaCapabilitySnapshot {
  status: string;
  endpoint: string;
  configuredModels: string[];
  installedModels: string[];
  loadedModels: string[];
  providerReachable: boolean;
  configuredModelMissing: boolean;
  reason: string | null;
}

export interface HardwareCapabilitySummary {
  cpu: string;
  memory: string;
  gpu: string;
  cuda: string;
  vram: string;
  provider: string;
}
