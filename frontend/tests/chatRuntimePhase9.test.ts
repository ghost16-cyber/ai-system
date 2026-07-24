import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  describeGenerationProvenance,
  describeRetrievalMode,
  summarizeChatCitations,
} from "../src/state/chatRuntimeState.ts";
import type { ChatRunResponse } from "../src/clients/astraClient.ts";

const run = (overrides: Partial<ChatRunResponse> = {}): ChatRunResponse => ({
  run_id: "run-1",
  conversation_id: "conversation-1",
  user_message: "Explain the code in this repo",
  assistant_response: "It parses and normalizes values.",
  selected_specialist: "rag_specialist",
  intent: "rag",
  confidence: 0.8,
  rag_used: false,
  rag_skip_reason: null,
  rag_context_count: 0,
  runtime_decision: "fallback",
  safety_decision: "allow",
  used_real_slm: false,
  slm_provider: "fallback",
  slm_model: null,
  slm_fallback_reason: null,
  slm_latency_ms: null,
  memory_used: false,
  memory_summary: null,
  created_at: "2026-01-01T00:00:00Z",
  trace_summary: [],
  ...overrides,
});

test("summarizeChatCitations reduces rag_sources into path + line range + score", () => {
  const citations = summarizeChatCitations(run({
    rag_sources: [
      { path: "src/parser.py", start_line: 1, end_line: 3, score: 0.9 },
      { path: "src/other.py", start_line: null, end_line: null, score: 0.5 },
    ],
  }));

  assert.deepEqual(citations, [
    { path: "src/parser.py", lineRange: "1-3", score: 0.9 },
    { path: "src/other.py", lineRange: null, score: 0.5 },
  ]);
});

test("summarizeChatCitations returns an empty list when rag_sources is absent", () => {
  assert.deepEqual(summarizeChatCitations(run()), []);
});

test("describeRetrievalMode reports project-bound retrieval when rag_used is true", () => {
  assert.equal(
    describeRetrievalMode(run({ rag_used: true, rag_context_count: 1 })),
    "Project-bound retrieval",
  );
});

test("describeRetrievalMode reports a typed skip reason when retrieval never ran", () => {
  assert.equal(
    describeRetrievalMode(run({ rag_skip_reason: "no_canonical_project" })),
    "No retrieval (no canonical project)",
  );
});

test("describeRetrievalMode falls back honestly when nothing is known", () => {
  assert.equal(describeRetrievalMode(run()), "No retrieval");
});

test("describeGenerationProvenance flattens the run's local-AI fields", () => {
  const provenance = describeGenerationProvenance(run({
    used_real_slm: true,
    slm_provider: "ollama",
    slm_model: "qwen2.5-coder:1.5b",
    slm_latency_ms: 12,
  }));

  assert.deepEqual(provenance, {
    usedLocalAI: true,
    provider: "ollama",
    model: "qwen2.5-coder:1.5b",
    fallbackReason: null,
    latencyMs: 12,
  });
});

test("App.tsx surfaces chat runtime lineage via the typed state module, not local recomputation", () => {
  const app = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");
  assert.match(app, /describeGenerationProvenance/);
  assert.match(app, /describeRetrievalMode/);
  assert.match(app, /summarizeChatCitations/);
});

test("the legacy chat-native SLM-profile-mutation path has been removed", () => {
  const app = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");
  assert.doesNotMatch(app, /change the selected model/);
  assert.doesNotMatch(app, /selectSlmProfile/);
  assert.doesNotMatch(app, /system_configuration/);

  const client = readFileSync(new URL("../src/clients/astraClient.ts", import.meta.url), "utf8");
  assert.doesNotMatch(client, /selectSlmProfile/);
  assert.doesNotMatch(client, /getSlmProfiles/);
});
