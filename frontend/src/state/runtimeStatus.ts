import type {
  RuntimeCorpusStatus,
  RuntimeHealthReport,
  RuntimeJobsStatus,
  RuntimeReadiness,
  RuntimeSnapshot,
} from "../clients/astraClient";

export interface RuntimeStatusSummary {
  runtimeReady: boolean;
  runtimeState: string;
  corpusReady: boolean | null;
  provider: string;
  retrievalMode: string;
  backgroundIndexing: boolean;
  recoveryState: "none" | "pending" | "unknown";
  blockingReasons: string[];
}

/** Pure, presentation-ready summary for the read-only runtime status rows.
 * Never exposes implementation details (subsystem internals, raw
 * blocking-reason codes beyond what's already user-facing) or any approval
 * control -- this mirrors backend read models as-is (see CLAUDE.md), it
 * never recomputes authority locally. */
export function summarizeRuntimeStatus(
  readiness: RuntimeReadiness | null,
  health: RuntimeHealthReport | null,
  corpus: RuntimeCorpusStatus | null,
  jobs: RuntimeJobsStatus | null,
): RuntimeStatusSummary {
  const providerHealth = health?.subsystems.find(
    (item) => item.subsystem_id === "rag_embedding_provider",
  );
  return {
    runtimeReady: readiness?.ready ?? false,
    runtimeState: readiness?.state ?? "stopped",
    corpusReady: corpus ? corpus.freshness.fresh : (readiness?.corpus_valid ?? null),
    provider: providerHealth ? providerHealth.status : "unknown",
    retrievalMode: readiness?.retrieval_ready ? "ready" : "unavailable",
    backgroundIndexing: (jobs?.queued ?? 0) > 0 || (jobs?.claimed ?? 0) > 0 || (jobs?.running ?? 0) > 0,
    recoveryState: readiness == null ? "unknown" : readiness.pending_recovery ? "pending" : "none",
    blockingReasons: readiness ? [...readiness.blocking_reasons] : [],
  };
}

export function latestTransitionLabel(snapshot: RuntimeSnapshot | null): string | null {
  if (!snapshot || snapshot.recent_transitions.length === 0) return null;
  const latest = snapshot.recent_transitions[snapshot.recent_transitions.length - 1];
  return `${latest.from_state} -> ${latest.to_state}`;
}
