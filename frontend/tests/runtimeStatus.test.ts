import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { latestTransitionLabel, summarizeRuntimeStatus } from "../src/state/runtimeStatus.ts";
import type {
  RuntimeCorpusStatus,
  RuntimeHealthReport,
  RuntimeJobsStatus,
  RuntimeReadiness,
  RuntimeSnapshot,
} from "../src/clients/astraClient.ts";

const readiness = (overrides: Partial<RuntimeReadiness> = {}): RuntimeReadiness => ({
  schema_version: "astra.runtime.readiness.v1",
  ready: true,
  state: "ready",
  blocking_reasons: [],
  control_ready: true,
  retrieval_ready: true,
  corpus_valid: null,
  providers_healthy: true,
  pending_recovery: false,
  project_id: null,
  generated_at: "2026-01-01T00:00:00Z",
  ...overrides,
});

const health = (overrides: Partial<RuntimeHealthReport> = {}): RuntimeHealthReport => ({
  schema_version: "astra.runtime.health-report.v1",
  runtime_state: "ready",
  overall: "ready",
  subsystems: [],
  generated_at: "2026-01-01T00:00:00Z",
  ...overrides,
});

test("summarizes a fully healthy runtime", () => {
  const summary = summarizeRuntimeStatus(readiness(), health({
    subsystems: [{
      schema_version: "astra.runtime.subsystem-health.v1",
      subsystem_id: "rag_embedding_provider",
      capability_id: "rag_embedding_provider",
      status: "ready",
      details: {},
      probed_at: "2026-01-01T00:00:00Z",
    }],
  }), null, null);

  assert.equal(summary.runtimeReady, true);
  assert.equal(summary.runtimeState, "ready");
  assert.equal(summary.provider, "ready");
  assert.equal(summary.retrievalMode, "ready");
  assert.equal(summary.recoveryState, "none");
  assert.equal(summary.backgroundIndexing, false);
  assert.deepEqual(summary.blockingReasons, []);
});

test("reports degraded state and blocking reasons honestly", () => {
  const summary = summarizeRuntimeStatus(
    readiness({ ready: false, state: "degraded", blocking_reasons: ["providers_not_healthy"], pending_recovery: true }),
    health(),
    null,
    null,
  );
  assert.equal(summary.runtimeReady, false);
  assert.equal(summary.runtimeState, "degraded");
  assert.equal(summary.recoveryState, "pending");
  assert.deepEqual(summary.blockingReasons, ["providers_not_healthy"]);
});

test("corpus status overrides readiness corpus_valid when a project is selected", () => {
  const corpus: RuntimeCorpusStatus = {
    schema_version: "astra.runtime.corpus-status.v1",
    project_id: "project-1",
    freshness: {
      schema_version: "astra.runtime.corpus-freshness.v1",
      project_id: "project-1",
      fresh: false,
      reason: "invalidated",
      repository_changed: false,
      current_generation_id: "gen-1",
      checked_at: "2026-01-01T00:00:00Z",
    },
    reindex_scheduled: true,
    generated_at: "2026-01-01T00:00:00Z",
  };
  const summary = summarizeRuntimeStatus(readiness({ corpus_valid: true }), health(), corpus, null);
  assert.equal(summary.corpusReady, false);
});

test("without any project context corpus readiness is honestly unknown, not fabricated", () => {
  const summary = summarizeRuntimeStatus(readiness({ corpus_valid: null }), health(), null, null);
  assert.equal(summary.corpusReady, null);
});

test("background indexing reflects queued, claimed, or running jobs", () => {
  const jobs: RuntimeJobsStatus = {
    schema_version: "astra.runtime.jobs-status.v1",
    queued: 0,
    claimed: 1,
    running: 0,
    recent: [],
    generated_at: "2026-01-01T00:00:00Z",
  };
  const summary = summarizeRuntimeStatus(readiness(), health(), null, jobs);
  assert.equal(summary.backgroundIndexing, true);
});

test("missing readiness/health data never fabricates a ready state", () => {
  const summary = summarizeRuntimeStatus(null, null, null, null);
  assert.equal(summary.runtimeReady, false);
  assert.equal(summary.runtimeState, "stopped");
  assert.equal(summary.recoveryState, "unknown");
  assert.equal(summary.provider, "unknown");
});

test("latestTransitionLabel reads the most recent transition only", () => {
  const snapshot: RuntimeSnapshot = {
    schema_version: "astra.runtime.snapshot.v1",
    state: "ready",
    recent_transitions: [
      {
        schema_version: "astra.runtime.state-transition.v1",
        from_state: "stopped", to_state: "initializing", trigger: "startup",
        reason: null, occurred_at: "2026-01-01T00:00:00Z",
      },
      {
        schema_version: "astra.runtime.state-transition.v1",
        from_state: "initializing", to_state: "ready", trigger: "startup_complete",
        reason: null, occurred_at: "2026-01-01T00:00:01Z",
      },
    ],
    generated_at: "2026-01-01T00:00:01Z",
  };
  assert.equal(latestTransitionLabel(snapshot), "initializing -> ready");
  assert.equal(latestTransitionLabel(null), null);
});

test("App.tsx surfaces runtime status via the typed runtime client, not local recomputation", () => {
  const app = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");
  assert.match(app, /getRuntimeStatus/);
  assert.match(app, /getRuntimeReadiness/);
  assert.match(app, /getRuntimeHealth/);
  assert.match(app, /getRuntimeJobs/);
  assert.match(app, /summarizeRuntimeStatus/);
  assert.match(app, /Runtime Ready/);
  assert.match(app, /Corpus Ready/);
  assert.match(app, /Recovery state/);
});

test("no approval-control affordances are wired into the runtime status surface", () => {
  const state = readFileSync(new URL("../src/state/runtimeStatus.ts", import.meta.url), "utf8");
  assert.doesNotMatch(state, /approve|approval_token|postJson/i);
});
