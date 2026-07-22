# Astra Phase 5A: production-safe local generation gateway

## Status and scope

Phase 5A adds an internal, advisory-only gateway for bounded structured generation with an exact configured Ollama model. It does not add a frontend or public generation endpoint, RAG indexing, training, model installation, automatic Ollama startup, project mutation, command execution, approval, verification, or handoff authority.

## Architecture

`backend.app.local_ai.config` remains the sole environment-configuration authority. `OllamaProviderClient` is the only backend module that performs Ollama HTTP requests. `LocalGenerationGateway` owns readiness, request-policy checks, durable invocation records, exact replay, strict JSON parsing, and target-contract validation. Existing local-AI capability and legacy client adapters now reuse the provider boundary instead of issuing their own HTTP requests.

The gateway returns data only. A successful result has `advisory_only=true` and `authority_granted=false`. Later synthesis code must independently validate the proposal and submit it through the canonical project control and approval flow. The gateway imports no worker, Docker, mutation, approval, verification, or handoff service.

## Configuration authority

The versioned `astra.local-ai.configuration.v2` contract provides:

- provider type and canonical endpoint identity;
- role-specific synthesis, coding, planning, and review model tags;
- connection and generation timeouts;
- maximum context and output-token limits;
- `generation_enabled`, read from `ASTRA_LOCAL_AI_GENERATION_ENABLED` with the legacy `ASTRA_SLM_ENABLED` accepted only inside the canonical loader.

The generation service does not read environment variables. Arbitrary request endpoints and arbitrary model substitution are not supported.

## Provider boundary and exact-model readiness

The stdlib-only provider client reads `/api/version`, `/api/tags`, and `/api/ps`, and submits bounded non-streaming generation to `/api/generate`. Responses are byte-bounded before JSON decoding. Transport failure, timeout, cancellation, HTTP rejection, and malformed envelopes are typed.

Before every new generation, the gateway verifies provider reachability and requires exact string membership of the role-configured model tag in the installed-model inventory. A missing tag, `latest` alias, similar prefix, larger model, or smaller model is never selected automatically. Ollama is never started and a model is never pulled.

## Request and result contracts

`astra.local-ai.generation-request.v1` requires a request ID, idempotency key, purpose, exact model tag, system instruction, user content, optional bounded context items, expected response-schema identity, timeout, bounded parameters, and typed bounded correlation metadata.

The contract rejects input beyond the fixed system, user, item, item-count, context-total, and serialized-request limits. The gateway additionally enforces canonical context-token, output-token, timeout, role, and model limits. Evidence-bearing input is never silently truncated.

`astra.local-ai.generation-result.v1` records provider and endpoint identity, exact model, timestamps, duration, terminal state, response hash, validated structured output, bounded Ollama evaluation counters, typed failure, and replay metadata. Raw prompts and raw provider envelopes are not returned or stored by the invocation ledger.

## Structured-output validation

Canonical Phase 5A calls request Ollama JSON format. The returned response string must contain exactly one JSON object, apart from harmless surrounding JSON whitespace. Markdown fences, prose mixed with JSON, malformed JSON, arrays, and scalars fail. The parsed object must then validate against the caller-supplied strict target model, including its exact schema-version identity. JSON syntax alone never grants semantic trust, and returned command or patch strings remain inert data.

## Persistence and idempotency

Migration 12 creates `local_ai_generation_invocations`, request/status indexes, and a trigger that permits only the initial `started` to terminal transition while keeping identity fields fixed. Completed and failed terminal rows cannot be updated.

The record stores request, input, context, and response hashes plus bounded diagnostics; it does not store the full prompt or unrestricted raw response. A successful result stores only its already bounded, target-validated object.

The idempotency key is unique within the local-generation domain. The same key and normalized request fingerprint returns the stored completed result with `replayed=true` and makes no provider call. A changed fingerprint conflicts. Failed, cancelled, timed-out, interrupted, or otherwise non-completed records never replay as success. This ledger is deliberately separate from `project_action_replays` and carries no project authority.

## Failure taxonomy and observability

Typed failures cover disabled local AI, unsupported provider, unreachable provider, unavailable exact model, invalid or oversized request, timeout, cancellation, provider rejection, malformed envelope, invalid JSON structure, target-schema failure, idempotency conflict, persistence failure, and internal failure. Safe user messages are separated from bounded diagnostic metadata.

The existing local-AI audit ledger receives readiness, started, completed, failed, replayed, conflict, timeout, and cancellation events. Events contain identities, hashes, counts, and classifications only—not prompts, source code, credentials, or unrestricted output.

## Test evidence

Deterministic Phase 5A tests use injected fake providers and mocked stdlib HTTP responses. They cover configuration, exact readiness, bounds, strict parsing and target validation, provider failures, persistence, immutable terminal records, exact replay, authority isolation, and Migration 12 shape ownership. Automated tests do not contact a live Ollama instance.

Exact command results are recorded in the implementation handoff rather than hardcoded here, so this document cannot become an outdated test-count authority.

## Known limitations and Phase 5B integration

- Cooperative cancellation is checked immediately before and after the blocking stdlib request; timeout is the in-flight bound. A future streaming transport may provide finer cancellation granularity.
- An interrupted `started` invocation fails closed and requires explicit operational recovery; it is never guessed to have succeeded.
- Phase 5A does not wire model output into project synthesis or RAG. Phase 5B may adapt validated proposal contracts to immutable synthesis previews, but must preserve canonical artifact binding and explicit approval.
- Real-model output quality, latency, VRAM use, and Qwen compatibility require the user's manual hardware check.

## User-run Ollama verification

Run these later, only after starting Ollama and ensuring the approved exact model is already installed:

```bash
export ASTRA_LOCAL_AI_GENERATION_ENABLED=true
export ASTRA_LOCAL_AI_PROVIDER=ollama
export ASTRA_LOCAL_AI_MODEL=qwen2.5-coder:1.5b
export ASTRA_OLLAMA_ENDPOINT=http://127.0.0.1:11434

ollama list
curl --fail --silent http://127.0.0.1:11434/api/version
curl --fail --silent http://127.0.0.1:11434/api/tags
```

Confirm that `/api/tags` contains the exact configured tag. Do not run `ollama pull` as part of verification. A real gateway smoke call should use a disposable migrated database and a strict non-authoritative target schema; it must not be performed against a project workflow until Phase 5B integration is reviewed.
