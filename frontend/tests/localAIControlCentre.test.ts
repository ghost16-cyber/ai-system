import assert from "node:assert/strict";
import test from "node:test";

import type { LocalAICapabilityReport, LocalAIModelConfiguration } from "../src/clients/astraClient.ts";
import {
  classifyLocalAIError,
  classifyOverallStatus,
  diagnoseLocalAvailability,
  findConfiguredModel,
  initialLocalAIState,
  localAIReducer,
  ollamaCapability,
  overallStatusLabel,
  summarizeHardware,
} from "../src/features/localAI/state/localAIState.ts";

function model(overrides: Partial<LocalAIModelConfiguration> = {}): LocalAIModelConfiguration {
  return {
    model_profile_id: "configured-local-model",
    display_name: "Qwen2.5 Coder 1.5B",
    provider_id: "ollama-local",
    local_available: false,
    enabled: false,
    policy_status: "not_configured",
    intended_roles: [],
    source_metadata: {},
    configuration_version: 11,
    updated_at: "2026-07-23T18:24:13.562449+00:00",
    ...overrides,
  };
}

/** Mirrors the exact shape `AstraHttpError` throws (`status`/`message`)
 * without importing the class itself -- that class uses a TypeScript
 * parameter-property constructor, which node's type-stripping test runner
 * cannot execute. `classifyLocalAIError` duck-types this same shape, so a
 * plain object exercises the identical code path a real thrown error would. */
function httpError(status: number, detail: unknown): { status: number; message: string } {
  return { status, message: JSON.stringify({ detail }) };
}

function capabilities(overrides: Partial<LocalAICapabilityReport> = {}): LocalAICapabilityReport {
  return {
    schema_version: "astra.local-ai.host-report.v1",
    report_id: "cap-abc123",
    os_name: "Linux",
    architecture: "x86_64",
    python_version: "3.11.0",
    wsl: true,
    capabilities: [
      { capability_id: "cpu", status: "available", details: {} },
      { capability_id: "memory", status: "available", details: {} },
      { capability_id: "gpu", status: "available", details: {} },
      { capability_id: "cuda", status: "available", details: {} },
      { capability_id: "vram", status: "available", details: {} },
      {
        capability_id: "ollama", status: "available", details: {},
        endpoint: "http://127.0.0.1:11434",
        configured_models: ["qwen2.5-coder:1.5b"],
        installed_models: ["qwen2.5-coder:1.5b"],
        loaded_models: [],
        provider_reachable: true,
        configured_model_missing: false,
      },
    ],
    disk: {},
    warnings: [],
    generated_at: "2026-07-26T12:00:00.000000+00:00",
    ...overrides,
  };
}

test("initial load: idle before load, ready with models and capabilities after load_success", () => {
  assert.equal(initialLocalAIState.status, "idle");
  const started = localAIReducer(initialLocalAIState, { type: "load_start" });
  assert.equal(started.status, "loading");

  const configured = model({ enabled: true, policy_status: "ready", local_available: true });
  const report = capabilities();
  const loaded = localAIReducer(started, { type: "load_success", models: [configured], capabilities: report });
  assert.equal(loaded.status, "ready");
  assert.deepEqual(loaded.models, [configured]);
  assert.equal(loaded.capabilities, report);
  assert.equal(loaded.error, null);
});

test("refresh: refreshing flag toggles and capabilities are replaced without touching models", () => {
  const configured = model();
  const loaded = { ...initialLocalAIState, status: "ready" as const, models: [configured] };
  const started = localAIReducer(loaded, { type: "refresh_start" });
  assert.equal(started.refreshing, true);

  const refreshedReport = capabilities({ report_id: "cap-new456" });
  const done = localAIReducer(started, { type: "refresh_success", capabilities: refreshedReport });
  assert.equal(done.refreshing, false);
  assert.equal(done.capabilities, refreshedReport);
  assert.deepEqual(done.models, [configured]);
});

test("enable: the row is replaced by exactly the server response, never a locally computed version", () => {
  const before = model({ enabled: false, configuration_version: 11 });
  const state = { ...initialLocalAIState, models: [before] };
  const starting = localAIReducer(state, { type: "model_action_start", modelProfileId: before.model_profile_id });
  assert.deepEqual(starting.pendingModelIds, [before.model_profile_id]);

  // A deliberately "surprising" server version (not before.configuration_version + 1) proves
  // the reducer copies the response verbatim instead of incrementing anything itself.
  const serverResponse = model({ enabled: true, policy_status: "ready", local_available: true, configuration_version: 47 });
  const enabled = localAIReducer(starting, { type: "model_action_success", model: serverResponse });
  assert.deepEqual(enabled.pendingModelIds, []);
  assert.equal(enabled.models.length, 1);
  assert.equal(enabled.models[0].enabled, true);
  assert.equal(enabled.models[0].configuration_version, 47);
  assert.equal(enabled.models[0], serverResponse);
});

test("disable: symmetric with enable, row replaced by the server response", () => {
  const before = model({ enabled: true, configuration_version: 12, policy_status: "ready" });
  const state = { ...initialLocalAIState, models: [before] };
  const starting = localAIReducer(state, { type: "model_action_start", modelProfileId: before.model_profile_id });
  const serverResponse = model({ enabled: false, configuration_version: 13, policy_status: "installed_not_enabled" });
  const disabled = localAIReducer(starting, { type: "model_action_success", model: serverResponse });
  assert.equal(disabled.models[0].enabled, false);
  assert.equal(disabled.models[0].configuration_version, 13);
  assert.deepEqual(disabled.pendingModelIds, []);
});

test("409 conflict: stale_configuration_version classifies to the exact required message and never mutates the row", () => {
  const error = httpError(409, { code: "stale_configuration_version" });
  const classified = classifyLocalAIError(error);
  assert.equal(classified.kind, "stale_version");
  assert.equal(classified.message, "Configuration changed. Reload configuration.");

  const before = model({ configuration_version: 11 });
  const state = { ...initialLocalAIState, models: [before], pendingModelIds: [before.model_profile_id] };
  const failed = localAIReducer(state, {
    type: "model_action_error", modelProfileId: before.model_profile_id, error: classified,
  });
  assert.deepEqual(failed.pendingModelIds, []);
  assert.equal(failed.error, classified);
  assert.deepEqual(failed.models, [before]); // unchanged -- no optimistic mutation was ever applied
});

test("provider unavailable: ambient policy_status maps to the Provider unreachable overall status", () => {
  const unreachable = model({ policy_status: "provider_unreachable" });
  assert.equal(classifyOverallStatus(unreachable), "provider_unreachable");
  assert.equal(overallStatusLabel(classifyOverallStatus(unreachable)), "Provider unreachable");
});

test("model_not_locally_available uses neutral wording, not always \"Ollama unreachable\"", () => {
  // This single backend error code covers several distinct causes (unreachable
  // provider, missing model, stale snapshot, another admission condition) --
  // the mutation-error message itself must stay neutral; the specific cause
  // is a *separate* read of the capability snapshot (see diagnoseLocalAvailability below).
  const error = httpError(409, { code: "model_not_locally_available" });
  const classified = classifyLocalAIError(error);
  assert.equal(classified.kind, "model_unavailable");
  assert.equal(
    classified.message,
    "The configured model is not currently available locally. Refresh capabilities and confirm the model is installed.",
  );
});

test("diagnoseLocalAvailability reads the capability snapshot to show the specific cause separately", () => {
  const unreachable = capabilities({
    capabilities: [
      {
        capability_id: "ollama", status: "unavailable", details: {},
        endpoint: "http://127.0.0.1:11434", configured_models: ["qwen2.5-coder:1.5b"],
        installed_models: [], loaded_models: [], provider_reachable: false, configured_model_missing: false,
      },
    ],
  });
  assert.equal(diagnoseLocalAvailability(unreachable), "Ollama is not reachable.");

  const missingModel = capabilities({
    capabilities: [
      {
        capability_id: "ollama", status: "unavailable", details: {},
        endpoint: "http://127.0.0.1:11434", configured_models: ["qwen2.5-coder:1.5b"],
        installed_models: [], loaded_models: [], provider_reachable: true, configured_model_missing: true,
      },
    ],
  });
  assert.equal(diagnoseLocalAvailability(missingModel), "The configured model is not installed.");

  const otherAdmissionFailure = capabilities({
    capabilities: [
      {
        capability_id: "ollama", status: "unavailable", details: {},
        endpoint: "http://127.0.0.1:11434", configured_models: ["qwen2.5-coder:1.5b"],
        installed_models: ["qwen2.5-coder:1.5b"], loaded_models: [], provider_reachable: true, configured_model_missing: false,
      },
    ],
  });
  assert.equal(diagnoseLocalAvailability(otherAdmissionFailure), "The model is not currently available locally.");
  assert.equal(diagnoseLocalAvailability(null), "The model is not currently available locally.");
});

test("model missing and disabled and ready and unavailable all map from policy_status, never guessed", () => {
  assert.equal(classifyOverallStatus(model({ policy_status: "model_not_installed" })), "model_missing");
  assert.equal(classifyOverallStatus(model({ policy_status: "installed_not_enabled" })), "disabled");
  assert.equal(classifyOverallStatus(model({ policy_status: "ready" })), "ready");
  assert.equal(classifyOverallStatus(model({ policy_status: "insufficient_vram" })), "unavailable");
  assert.equal(classifyOverallStatus(undefined), "unavailable");
});

test("network failure: a non-HTTP error (e.g. fetch rejecting) is classified as unable to contact the backend", () => {
  const classified = classifyLocalAIError(new TypeError("Failed to fetch"));
  assert.equal(classified.kind, "network");
  assert.equal(classified.message, "Unable to contact Astra backend.");
});

test("loading: status starts idle, becomes loading immediately on load_start, with no models yet", () => {
  assert.equal(initialLocalAIState.status, "idle");
  assert.deepEqual(initialLocalAIState.models, []);
  const started = localAIReducer(initialLocalAIState, { type: "load_start" });
  assert.equal(started.status, "loading");
  assert.deepEqual(started.models, []);
});

test("diagnostics expansion: toggling is a pure, idempotent flip and exposes the exact required fields", () => {
  const closed = initialLocalAIState;
  assert.equal(closed.diagnosticsOpen, false);
  const opened = localAIReducer(closed, { type: "toggle_diagnostics" });
  assert.equal(opened.diagnosticsOpen, true);
  const reClosed = localAIReducer(opened, { type: "toggle_diagnostics" });
  assert.equal(reClosed.diagnosticsOpen, false);

  const report = capabilities();
  const snapshot = ollamaCapability(report);
  assert.equal(snapshot?.endpoint, "http://127.0.0.1:11434");
  assert.deepEqual(snapshot?.installedModels, ["qwen2.5-coder:1.5b"]);
  assert.deepEqual(snapshot?.loadedModels, []);
  assert.equal(snapshot?.providerReachable, true);
  assert.equal(report.report_id, "cap-abc123");
});

test("findConfiguredModel prefers the configured-local-model id over the enabled-fallback heuristic", () => {
  const configured = model({ model_profile_id: "configured-local-model", enabled: false });
  const other = model({ model_profile_id: "qwen3-4b-q4-k-m", enabled: true, provider_id: "ollama-local" });
  assert.equal(findConfiguredModel([other, configured]), configured);
  assert.equal(findConfiguredModel([other]), other);
  assert.equal(findConfiguredModel([]), undefined);
});

test("summarizeHardware reads each capability_id independently and defaults to unknown when absent", () => {
  const summary = summarizeHardware(capabilities());
  assert.equal(summary.cpu, "available");
  assert.equal(summary.memory, "available");
  assert.equal(summary.gpu, "available");
  assert.equal(summary.cuda, "available");
  assert.equal(summary.vram, "available");
  assert.equal(summary.provider, "available");
  assert.deepEqual(summarizeHardware(null), {
    cpu: "unknown", memory: "unknown", gpu: "unknown", cuda: "unknown", vram: "unknown", provider: "unknown",
  });
});
