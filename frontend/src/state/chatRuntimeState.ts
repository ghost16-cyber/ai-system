import type { ChatRunResponse } from "../clients/astraClient";

export interface ChatCitation {
  path: string;
  lineRange: string | null;
  score: number;
}

/** Reduces a run's structured rag_sources into display-ready citations --
 * pure formatting only, never re-derives which sources were used. */
export function summarizeChatCitations(run: ChatRunResponse): ChatCitation[] {
  return (run.rag_sources ?? []).map((source) => ({
    path: source.path,
    lineRange:
      source.start_line != null && source.end_line != null
        ? `${source.start_line}-${source.end_line}`
        : null,
    score: source.score,
  }));
}

/** Honest, one-line description of why retrieval did or didn't run --
 * mirrors the backend's own rag_used/retrieval_mode/rag_skip_reason fields
 * rather than re-guessing from message content. */
export function describeRetrievalMode(run: ChatRunResponse): string {
  if (run.rag_used) return "Project-bound retrieval";
  if (run.retrieval_mode === "canonical_project") return "Project-bound retrieval (no results)";
  if (run.rag_skip_reason) return `No retrieval (${run.rag_skip_reason.replace(/_/g, " ")})`;
  return "No retrieval";
}

export interface ChatGenerationProvenance {
  usedLocalAI: boolean;
  provider: string;
  model: string | null;
  fallbackReason: string | null;
  latencyMs: number | null;
}

/** Flattens a run's local-AI generation fields into one typed summary for
 * display -- the run itself remains the sole source of truth. */
export function describeGenerationProvenance(run: ChatRunResponse): ChatGenerationProvenance {
  return {
    usedLocalAI: run.used_real_slm,
    provider: run.slm_provider,
    model: run.slm_model,
    fallbackReason: run.slm_fallback_reason,
    latencyMs: run.slm_latency_ms,
  };
}
