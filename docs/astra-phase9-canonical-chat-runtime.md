# Astra Phase 9: Canonical Chat Runtime Integration

## Scope

Phase 9 replaces chat's direct calls to the legacy `slm.gateway.chat_with_slm` and
generic `rag.context_service.rag_search` with a single orchestration service,
`CanonicalChatRuntimeService` (`backend/app/chat_runtime/service.py`), that composes
already-implemented canonical authorities only:

- `RuntimeManager` (Phase 8) for readiness.
- `ProjectRetrievalService` (Phase 6/7) for retrieval — project-bound only.
- `LocalAIService.execute_generation` (Stage 7D) for every local generation, via a new
  `GenerationPurpose.CHAT`.
- Durable chat-runtime lineage, persisted to `chat_runtime_links`.

`CanonicalChatRuntimeService` creates no new authority: no project mutation, no
approval, no model enablement, no corpus ingestion. Every path that does not reach a
real local generation produces a typed `ChatRuntimeFailure`
(`backend/app/chat_runtime/contracts.py`) instead of an exception surfaced to chat.

`rag.corpus_retrieval` (the separate persistent-corpus mechanism already integrated
into `chat_workflow.py`) is untouched by this phase; its content is threaded into the
canonical generation call via `CanonicalChatRuntimeService.answer(..., corpus_context=)`.

## What changed, and what didn't

**Changed:**
- `chat_workflow.py` no longer imports or calls `slm_gateway.chat_with_slm` or
  `rag_context_service.rag_search`. Its deterministic routing, greeting/system-meta
  detection, safety/runtime decisioning, and memory-continuity logic are unchanged —
  only the generation/retrieval calls were replaced.
- `/chat/run` and `/chat/stream` (`backend/app/main.py`) both create/claim a durable
  `chat_requests` row and call `run_chat_workflow(..., chat_runtime=chat_runtime_service,
  chat_request_id=..., lineage_sink=...)`, sharing one orchestration service and one
  request-identity model. `/chat/run` still auto-vivifies any `conversation_id` (its
  long-standing permissive behavior); `/chat/stream` still requires the conversation to
  already exist.
- `ChatRunRequest` gained an optional `project_run_id`. Retrieval only happens when a
  canonical project is selected — **there is no generic workspace-scan fallback**.
- `ChatRequestRecord` gained a `request_fingerprint` (a content hash of the stored
  request payload). `/chat/stream`'s exact-retry conflict check now compares the full
  fingerprint (message, conversation, project, RAG preference, and safety settings),
  not just conversation/message.

**Not changed:**
- The legacy `/analyze*`, `/orchestrate`, and other non-chat endpoints may still reach
  `slm_gateway`/`rag_context_service` — they are out of scope for this phase and remain
  available for compatibility.
- `RuntimeManager`, `LocalAIService`, `ProjectRetrievalService`, and
  `ProjectControlPlane` were not redesigned; Phase 9 only adds a chat-role resolution
  read method to `LocalAIService` (see below) and a thin recovery adapter wiring to
  `RuntimeManager`.

## `backend/app/chat_runtime/`

- **`contracts.py`** — `ChatResponseMode` (`local_ai` / `deterministic_fallback`),
  `ChatRuntimeFailureReason` (every `GenerationFailureReason` plus chat-specific
  absence reasons: `chat_role_not_configured`, `model_profile_not_found`,
  `model_profile_disabled`, `role_mapping_mismatch`, `admission_blocked`,
  `generation_in_progress`, `retrieval_unavailable`, ...), `ChatRuntimeGenerationSummary`,
  `ChatEvidenceCitation`, `ChatRuntimeRetrievalSummary`, `ChatRuntimeFailure`, and
  `ChatRuntimeLineage` — the exact, bounded shape persisted as
  `chat_runtime_links.lineage_json`.
- **`prompts.py`** — deterministic system-instruction/user-content/context-item
  construction. Retrieval evidence and persistent corpus context are passed as
  individually-attributed `GenerationContextItem`s, never inlined into free text.
- **`service.py`** — `CanonicalChatRuntimeService.answer(...)`: retrieves (if a project
  is bound), resolves the chat generation target, calls `execute_generation`, and
  returns a `ChatRuntimeAnswer` (assistant text or `None` on fallback, plus the
  `ChatRuntimeLineage` for persistence).

## Chat generation target resolution

`GenerationPurpose.CHAT` was added to `local_ai/generation_contracts.py`. A new
`chat_model` field on `LocalAIConfiguration` (`ASTRA_LOCAL_AI_CHAT_MODEL`) has **no
shared-model default** — unlike `planner`/`reviewer`, an unset chat role stays unset,
never seeded automatically.

`LocalAIService.resolve_chat_generation_target()` is the only sanctioned read path for
chat: it checks the static role mapping, matches an enabled model profile by exact
provider model ID, and cross-checks the optional durable `local_ai_role_mappings` row
if one exists. It returns a typed `ChatGenerationTarget` (status
`resolved`/`chat_role_not_configured`/`model_profile_not_found`/
`model_profile_disabled`/`role_mapping_mismatch`) — chat code never queries
`local_ai_*` tables directly.

## Idempotency and replay

Generation and retrieval idempotency keys derive deterministically from
`chat_request_id` + `request_fingerprint` (a content hash of the stored chat request
payload, excluding `request_id`). An exact retry (same request ID, same fingerprint)
reuses the stored scheduler job / generation / retrieval artifact through the existing
`execute_generation`/`retrieve` replay paths — `CanonicalChatRuntimeService` adds no
separate cache. `/chat/stream` also short-circuits entirely for a `completed` durable
request, returning the stored run without re-entering `chat_workflow` at all.

## Migration 18: `canonical_chat_runtime_lineage`

`chat_conversations`/`chat_requests`/`chat_runs` move under migration ownership
(adopted via `CREATE TABLE IF NOT EXISTS` plus `PRAGMA table_info`-guarded column
repairs, since they may already exist from `AnalysisRepository`'s legacy DDL on a real
database). `AnalysisRepository.initialize()` no longer creates or repairs these tables.

The new `chat_runtime_links` table is append-mostly: an update-protection trigger
blocks in-place tampering with a link's recorded lineage, but `ON DELETE CASCADE` (not
a delete-protection trigger) lets `delete_chat_conversation` purge links together with
their `chat_requests`/`chat_runs` rows — exactly how `chat_runs` itself has always been
purged on conversation deletion.

Startup recovery of chat requests left `active` by a prior process moved from an
implicit side effect of `AnalysisRepository.initialize()` to an explicit
`AnalysisRepository.recover_interrupted_chat_requests()`, wired into
`RuntimeManager`'s recovery pass via the repository's existing `SimpleInitAdapter`
(`backend/app/runtime/__init__.py`).

## Runtime background worker corrections

`RuntimeWorker.run_once()` now transitions a claimed job to `running` before invoking
its handler, so a crash mid-handler is observable on the next
`recover_expired_jobs()` pass rather than indistinguishable from "never started".
`RuntimeJobQueue.complete_job()` persists a bounded terminal `BackgroundJobResult`
(`succeeded`/`error`, error capped at 500 characters). `CorpusManager.reindex_scheduled()`
(surfaced via `RuntimeManager.corpus_status()`) now checks for an actual
queued/claimed/running `corpus_reindex` job for the target project
(`RuntimeJobQueue.has_active_job`), rather than reporting "not fresh" as "scheduled".

## Frontend

- `ChatRunRequest` gained `project_run_id`. `ChatRunResponse` needed no new fields —
  citations reuse the existing `rag_sources` shape, now populated by canonical
  retrieval instead of legacy free-text RAG.
- `frontend/src/state/chatRuntimeState.ts` — pure functions
  (`summarizeChatCitations`, `describeRetrievalMode`, `describeGenerationProvenance`)
  reducing a `ChatRunResponse` into display-ready citation/provenance data, wired into
  `RunDetails` in `App.tsx`.
- The chat-native "change the selected model" command (and its `system_configuration`
  action type, approval, and cancel handlers) was removed, along with the
  now-unreachable `getSlmProfiles`/`selectSlmProfile` client methods — model selection
  is no longer mutable from chat text.

## Explicit non-goals

No automatic model enablement, role assignment, corpus ingestion, package
installation, model download, execution, or approval was added anywhere in this phase.

**Update (Phase 9B):** a frontend project selector now exists (a compact control above
the composer, `frontend/src/state/chatProjectSelectionState.ts`) and the selection is
durably persisted per-conversation via `chat_conversations.active_project_run_id`
(migration 19, `PUT /chat/conversations/{conversation_id}/active-project`). Selecting a
project only updates this pointer — it creates no project, grants no approval, and does
not itself feed retrieval; the frontend still sends an explicit `project_run_id` on each
`/chat/run`/`/chat/stream` request, captured at request-creation time exactly as Phase 9
already required.
