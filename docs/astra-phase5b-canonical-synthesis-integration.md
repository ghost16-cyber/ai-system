# Astra Phase 5B — Canonical Model-Synthesis Integration

## Purpose and status

Phase 5B connects the production-safe Phase 5A local-generation boundary to the canonical project coordinator. Model output remains advisory data: it can create an immutable proposal and preview, but it cannot mutate files, execute commands, invoke Docker or workers, create approval grants, submit verification evidence, transition a project, or complete a handoff.

Project RAG, Transformers integration, training, model downloads, and public arbitrary-generation endpoints remain disabled or absent.

## Canonical synthesis flow

The reachable coordinator path is:

1. `ProjectControlPlane` records the canonical project and exact plan approval.
2. `ProjectCoordinatorService` emits a durable, exact-bound preparation intent.
3. Deterministic coordinator logic is attempted first.
4. Only when deterministic patch preparation cannot satisfy the approved work unit, the coordinator builds a bounded evidence artifact and claims the durable intent.
5. `CanonicalSynthesisOrchestrator` checks project, scope, plan, manifest, state-version, artifact, and intent freshness.
6. `Phase5ALocalSynthesisGateway` constructs a versioned request, derives the
   strict response JSON Schema, and calls the Phase 5A generation gateway using
   the configured provider endpoint and exact synthesis-model tag. Ollama
   receives that exact schema object through its `format` field.
7. Phase 5A validates the strict response contract and durably records the generation invocation before provider submission.
8. Phase 5B performs semantic scope checks and persists an immutable accepted or rejected proposal.
9. An accepted patch or repair becomes an immutable canonical preview artifact bound to the proposal fingerprint and evidence hash.
10. Existing exact artifact approval, durable worker, Docker-only execution, verification, and handoff authorities remain unchanged.

There is no host-execution fallback and no synthesis-side approval authority.

## Deterministic-first admission

The coordinator continues to use existing deterministic patch and repair operations before considering generation. Synthesis is blocked when the project or intent binding is stale, evidence has no approved paths, the exact provider profile differs from canonical configuration, generation or project synthesis is disabled, or the provider/model is unavailable. Project synthesis has its own explicit `ASTRA_PROJECT_SYNTHESIS_ENABLED` flag in addition to the Phase 5A generation flag.

## Evidence envelope

`astra.project-synthesis.evidence-envelope.v1` records the exact project/workspace, objective, scope and plan revisions, manifest and repository-state identities, deterministic evidence source, allowed/protected paths, constraints, permitted command categories, freshness identity, and content hash. It is canonical-JSON hashed and bounded to 196,608 bytes. Evidence items carry stable/source identities, provenance, trust classification, freshness identity, and their own exact content hash.

The trust enum intentionally contains no model-generated deterministic classification. Coordinator evidence redacts recognized secret-bearing fields before envelope construction. Repository content is serialized inside explicit untrusted-data delimiters by the Phase 5A adapter; no silent input truncation is permitted. RAG is fixed to `false` in the envelope and preview.

## Prompt templates and model selection

Patch/repair synthesis uses `astra.phase5b.patch-synthesis-prompt.v1`; diagnosis uses `astra.phase5b.diagnosis-prompt.v1`. The template version, full canonical request, expected schema identity, and canonical JSON Schema hash contribute to durable request and proposal bindings. Provider endpoint and exact role-specific model selection come only from `LocalAIConfiguration`; model output and callers cannot override them.

The prior direct `OllamaClient` project-synthesis path is retired. Production construction now returns a Phase 5A-backed adapter or a typed unavailable gateway. Test-only fake gateways remain dependency-injected and are not selectable through production environment mode.

## Proposal contracts and validation

The versioned proposal family contains:

- clarification: bounded questions, reason, blocking flag, and expected answer type;
- implementation plan: ordered work units, files, symbols, dependencies, acceptance criteria, validation, risks, and assumptions;
- patch: typed create/modify/delete operations, exact before-state identities, content, evidence references, validation, and risk;
- command: canonical category, structured argv, working-directory identity, purpose, timeout, and separate command approval classification;
- diagnosis: observed evidence, bounded confidence, probable cause, repair path, and missing evidence.

Strict Pydantic contracts reject additional or missing fields and mismatched proposal types. Semantic validators enforce matching project identity, complete/resolved evidence, normalized project-relative paths, protected and out-of-scope path rejection, plan dependency integrity and cycle rejection, structured non-shell commands, prohibited direct shell/container executables, evidence-backed diagnosis, bounded confidence, and non-duplicate clarifications. Existing synthesis-response validation additionally requires explicit evidence references and exact allowed paths before any preview is created.

Patch/repair is the currently reachable production coordinator generation path. Clarification is persisted when that path requires user input. Plan, command, and diagnosis contracts, validation, immutable storage, and read models are available for canonical producers; dedicated project UI actions for those proposal types are intentionally deferred rather than adding a parallel workflow.

## Immutable persistence and lifecycle

Migration 13, `canonical_model_synthesis_integration`, adds:

- `project_synthesis_proposals`;
- `project_synthesis_proposal_events`;
- project, generation, and lifecycle indexes;
- foreign keys and uniqueness for proposal identity, idempotency identity, fingerprint, and event sequence;
- database triggers preventing proposal/event update or deletion.

Migration 14, `ollama_json_schema_constrained_generation`, adds the exact
response-schema hash to the Phase 5A generation ledger and protects it with the
existing terminal-record immutability trigger.

The proposal JSON contains provider/endpoint/model identity, generation and request identities, request fingerprint, evidence identity/hash, repository state, revisions, prompt/schema identities, validation result, bounded rejection classification, immutable content, and proposal fingerprint. Exact replay returns the existing proposal. Reusing the idempotency key with changed request/evidence or proposal content fails closed.

The synthesis-owned lifecycle is append-only and bounded to generated, accepted, rejected, previewed, stale, superseded, and invalidated states. Synthesis cannot write approval, execution, verification, or completion states.

## Preview, approval, and replay binding

Accepted patch/repair proposals use the existing `ProjectArtifactStore`. Preview content and its binding include the proposal ID/fingerprint, evidence-envelope identity/hash, coordinator intent, manifest, plan and scope revisions, and the exact provider profile authority hash. The artifact content hash and binding hash therefore transitively cover the proposal and evidence.

Approval continues through `ProjectControlPlane` and its existing exact artifact policy. A changed proposal, evidence set, preview, manifest, revision, actor, workspace/conversation identity, state version, or approval type requires regeneration or re-approval. Patch approval does not authorize a command.

Phase 5A exact generation replay avoids a second provider call. Phase 5B exact proposal replay avoids a duplicate proposal. Coordinator invocation replay validates the referenced immutable preview. Replay cannot revive a stale proposal or create approval/execution authority.

## Read model

`GET /chat/projects/{project_run_id}/synthesis-proposals` is a read-only canonical endpoint. It checks that the project exists and returns bounded summaries with proposal type, lifecycle/validation status, safe model metadata, fingerprints, evidence hash, affected paths, and `advisory_only: true`. The frontend is not permitted to construct lifecycle or approval authority locally. A richer proposal panel and exact approval controls remain future UI work.

## Failure taxonomy

The integration fails closed with bounded classifications for disabled or unavailable providers, provider-profile mismatch, unsupported request/proposal contract, malformed request, invalid structured response, stale provider response, stale project/evidence binding, missing project, unresolved scope, oversized/invalid evidence, scope or evidence-reference violation, unsafe clarification, durable invocation conflict/in-progress state, corrupt replay reference, semantic rejection, proposal idempotency conflict, and persistence/preview failure. Provider raw output and full prompts are not written to user-facing diagnostics.

## Tests

The Phase 5B adversarial suite covers bounded deterministic evidence, prevention of model-derived trust, all five proposal contracts, strict type/field failures, path and scope attacks, protected paths, plan cycles, shell/container command attacks, diagnosis evidence/certainty, duplicate clarifications, immutable persistence/events, exact replay/conflict, Phase 5A replay, exact preview binding, and the absence of file mutation, worker dispatch, execution dispatch, or new approval grants.

Focused validation completed during implementation:

```text
python -m compileall -q backend
python -m pytest -q tests/test_project_synthesis_phase5b.py -x
21 passed

python -m pytest -q tests/test_database_migrations.py tests/test_local_ai_phase5a.py \
  tests/test_project_synthesis_phase5b.py tests/test_project_synthesis_orchestrator.py \
  tests/test_project_coordinator_synthesis.py tests/test_model_synthesis.py \
  tests/test_project_diagnosis.py -x
237 passed, 4 skipped
```

The final full non-Docker result is recorded in the completion report after execution. No Docker, live Ollama, model download, GPU, RAG, or training test is part of this phase.

A full non-Docker collection found 1,256 tests. Its execution was attempted with
`tests/test_project_worker_docker_integration.py` ignored, but the command hit
the 600-second execution ceiling before pytest emitted a terminal summary. It
reported no failure before timeout. This is not recorded as a pass; the exact
green compatibility result remains the 390-test gate above. The user may rerun
the full long-running gate without the automation time limit.

## Safe manual real-model verification

The user may run the disposable smoke script only after independently starting Ollama and installing the exact configured model:

```bash
cd /home/palla/projects/ai-system-1
source .venv/bin/activate
export ASTRA_LOCAL_AI_GENERATION_ENABLED=1
export ASTRA_PROJECT_SYNTHESIS_ENABLED=1
export ASTRA_LOCAL_AI_PROVIDER=ollama
export ASTRA_OLLAMA_ENDPOINT=http://127.0.0.1:11434
export ASTRA_LOCAL_AI_SYNTHESIS_MODEL='<exact-already-installed-model-tag>'
python scripts/astra_phase5b_smoke.py --confirm-advisory-generation
```

The script uses a temporary database and project, never starts or pulls Ollama, requests one harmless advisory proposal, verifies the disposable source hash is unchanged, verifies no additional approval/worker/dispatch authority was created, prints only the bounded diagnostic fields listed below, and deletes the temporary directory on exit. It was deliberately not run during automated validation.

On failure, the script prints only the failure classification, first bounded
Pydantic error location/type, provider/model/schema identity and hash, duration,
and prompt/output token counts. It never prints the prompt, evidence, secrets,
or raw model output.

## Known limitations and Phase 6 boundary

- Only patch/repair plus clarification is wired into the production coordinator fallback. Plan, command, and diagnosis have strict canonical contracts, validators, persistence, and read models but no new public generation action or full frontend presentation.
- Symbol resolution depends on deterministic evidence supplied by repository analysis; Phase 5B does not add a new language server or parser.
- Synthesis does not apply patches or execute proposed commands. Those remain canonical post-approval worker operations.
- The legacy project-job analysis API remains for compatibility, but its model boundary is redirected through Phase 5A. It does not gain canonical project authority.
- Real-model quality, latency, and resource fit are not asserted by fake-provider tests.

Phase 6 may add retrieval only behind the same deterministic evidence boundary. It must keep retrieved text untrusted, bounded, provenance-tagged, freshness-bound, and unable to grant authority. Phase 6 should not begin until Phase 5B focused and full non-Docker gates remain green and a user-run real-model smoke test is satisfactory. RAG remains disabled by default and by the current proposal envelope contract.
