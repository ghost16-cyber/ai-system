# Astra Pre-Phase 5 Integration Audit

Read-only architectural integration audit of the canonical project pipeline, performed
before Phase 5 (local-model generation) work begins.

- **Repository**: `~/projects/ai-system-1`
- **Branch**: `feature/chat-native-approval`
- **HEAD at audit start**: `b0e57b4` — "Add canonical local AI configuration and hardware modules"
- **Audit date**: 2026-07-22

## Executive verdict: **CONDITIONAL GO**

No reachable authority bypass, replay-authority defect, evidence-invalidation defect, or
migration-safety defect was found anywhere in the canonical pipeline (R5, R6, R7, R8, Phase
4A, Phase 4B). One P1 and two P2 defects were found and are now fixed, with regression tests,
in this pass. A handful of P2/P3 items remain as tracked, non-blocking follow-ups — none of
them let an untrusted input skip approval, bypass verification, mutate project files outside
the canonical worker, or corrupt/lose data. Phase 5 may proceed; the "Recommendations" section
lists what should be picked up alongside or shortly after it.

## Repository state

```
$ git status -sb
## feature/chat-native-approval...origin/feature/chat-native-approval [ahead 5]
?? CLAUDE.md
?? backend.zip / data.zip / scripts.zip / tools.zip / training.zip   (pre-existing, ignored per audit scope)
?? docs/*.docx, docs/astra-post-3h-remaining-work.md, docs/astra-through-stage-3h-implemented.md  (ignored per audit scope)
?? examples/                                                          (pre-existing, ignored per audit scope)

$ git log -12 --oneline
b0e57b4 Add canonical local AI configuration and hardware modules
807c86a Unify local AI configuration and hardware discovery
a554c3c Make evidence invalidation and action replay durable
88cbe6e Harden migration backup freshness validation
ee4b432 Retire reachable legacy host execution
365d77e Fix Linux available memory detection
4231d2d Add exact replay, manual verification, and local AI infrastructure
e9c7b1e Complete reliable canonical backend through Stage 3H
9de4cbd Make project creation and artifacts canonical
2e72fb5 Add versioned Astra project schema foundation
7ca047d Add Astra remaining implementation master plan
b71ca29 Add Stage 2C Docker build exclusions

$ git diff --stat
(empty — working tree was clean against HEAD before this audit's fixes)
```

`ee4b432` corresponds to the previously-completed R7 remediation (legacy host-execution
retirement); the four commits above it (`4231d2d`…`b0e57b4`) implement R5, R6, R8, and Phase
4A/4B and had not been independently audited before this pass.

## Method

Six areas were investigated in parallel by independent read-only sub-agents (R5 replay
authority, R6 manual verification, R7 re-verification post-`ee4b432`, R8 migration backup
freshness, Phase 4B local AI config + hardware, frontend authority), each required to cite
`file:line` evidence and a PASS/FAIL/UNCLEAR verdict per claim. Findings likely to carry real
risk (the dead `approve_manual_verification` wiring, the frontend dead-end suggestion) were
independently re-verified by direct code reads before being treated as fact. Invariant 1
(single execution authority, full pipeline structure) was audited directly. Full test suites,
`python -m compileall backend`, and `git diff --check` were run directly, not delegated.

## Architecture findings (Invariant 1 — single execution authority)

Confirmed structurally: `project_api/routes.py` depends only on `project_control` and
`project_coordinator` — no filesystem or subprocess access. `project_control/service.py` and
`project_coordinator/{service,execution}.py` contain **zero** `subprocess`/`Popen` calls
(verified by direct grep). `project_workers/__main__.py:build_runtime` wires the only path from
durable coordinator intents to actual execution: `execution_backend == "legacy"` **raises
unconditionally** ("Legacy host project execution has been retired... Astra will not fall back
to host execution"); `execution_backend == "docker"` is the only functioning path and requires
`DockerIsolationBackend.probe().available` before any work is dispatched — no host fallback on
probe failure. `subprocess`/`Popen` calls exist only inside `project_workers/isolation.py` and
`project_workers/execution.py` (the canonical Docker-isolated worker, explicitly out of scope
per the constraints) plus read-only tool/git/hardware probes elsewhere (`orchestrator/git_safety.py`,
`project_validation/workspace.py`, `local_ai/hardware.py`, `local_runtime/tool_detector.py`) and
dead code sitting behind the R7 retirement gates. The re-verification (Invariant 1, remaining
scope) is folded into the R7 section below.

## Invariant-by-invariant evidence

### 1. Single execution authority — PASS (see Architecture findings above + R7 below)

### 2. Replay authority (R5) — PASS, no defects

- `project_action_replays` (`database/migrations.py:504`) is the sole live completed-action
  authority. `ProjectControlPlane.has_idempotency_key`/`_idempotent_result`
  (`project_control/service.py:130-135`, `1312-1391`) are the only runtime read paths.
- Replay lookup (`_idempotent_result`) runs before every mutable-state check
  (`_execute_existing`, `service.py:541-556`): identity → replay lookup → `expected_state_version`
  → revision bindings → artifact currency, in that order.
- Exact replay returns the stored `TransitionResult`, cross-validated against the immutable
  `project_events` row on every read (`service.py:1358-1390`) — no re-execution, no blind trust
  of `replay_json` alone.
- Fingerprint mismatch (`request_fingerprint != request_hash`, hash covers the full command
  including artifact hashes) raises `IDEMPOTENCY_CONFLICT` (`service.py:1322-1329`), a typed
  fail-closed error. Confirmed by `tests/test_project_approval_binding.py::test_canonical_action_replay_fails_closed_on_unverifiable_persistence`.
- Replay cannot resurrect stale approval authority: a stored replay can only match a request
  byte-identical (including artifact hash/binding hash) to one that already passed
  `_verified_transition_artifact` when it originally executed; it re-serves history, it performs
  no new mutation. Explicitly documented in-code (`project_service.py:476-481`) and proven by
  `tests/test_project_approval_binding.py:361-389` (exact resend replays after supersession; a
  *new* idempotency key against the same stale artifact fails closed with `non_current_artifact`).
- `project_idempotency` and `project_action_replays` are genuinely different tables (not aliased
  terminology); the Phase 4A migration step backfills and cross-validates every legacy row into
  `project_action_replays`, then renames `project_idempotency` → `_legacy` (dead). No runtime code
  reads the legacy table.

### 3. Manual verification (R6) — PASS, one P2 defect found and fixed

- Evidence binds to exact criterion identity **and** content hashes
  (`ManualEvidenceSubmissionRequest`, `project_api/contracts.py:76-105`; enforced at
  `project_service.py:686-707`).
- Invalidations are immutable: `project_manual_evidence_invalidations.evidence_id` is `UNIQUE`,
  the only write is an idempotent `INSERT OR IGNORE` (`service.py:1806-1824`); no `UPDATE`/`DELETE`
  touches either evidence table anywhere in the codebase.
- Plan/scope/manifest changes invalidate stale evidence **eagerly**, synchronously, in the same
  transition (`service.py:606-616, 687-698, 770-804, 964-973, 1098-1109`), criterion-scoped so
  unrelated evidence survives.
- Handoff (`_validate_handoff`, `service.py:2002-2078`) re-queries invalidation state directly
  from the DB before permitting `REQUEST_HANDOFF` — a genuine validity check, not a presence check.
- **Defect (P2, fixed)**: a fully-wired but unreachable action, `approve_manual_verification`,
  mapped to `ProjectCommandType.APPROVE_COMMAND` — the exact shape of an already-fixed P1 bypass
  (`tests/test_project_approval_binding.py::test_command_approval_payload_cannot_forge_manual_verification_bypass`).
  It was inert only because no state transition ever set `pending_user_action` to that literal
  (independently confirmed by grep of all ~30 `pending_user_action` assignment sites in
  `service.py`). Any future change causing that string to become reachable would have silently
  reactivated a real bypass: a plain `APPROVE_COMMAND` grant with no criterion binding and no
  manual-evidence artifact requirement. See "Defects found" below.

### 4. Migration safety (R8) — PASS, no defects

- Backup uses SQLite's Online Backup API (`_prepare_migration_backup`, `database/migrations.py:1318-1402`),
  preceded by `PRAGMA wal_checkpoint(FULL)`, published atomically via `os.replace` — no raw
  `shutil.copy` path exists anywhere in the module.
- Source identity binding: `MigrationBackupManifest.source_database_identity` carries canonical
  path, device/inode, `source_sha256`, `source_logical_sha256` (hash of `iterdump()`), and schema
  versions — a real multi-attribute fingerprint.
- Both source and backup content are checksum-validated (byte hash + logical hash +
  `PRAGMA integrity_check` + schema-shape validation) before the backup is trusted.
- The manifest itself is structurally validated (field set, types, schema version) before any of
  its values are compared against anything.
- Stale-source revalidation happens **after** `BEGIN IMMEDIATE` acquires SQLite's write lock and
  immediately before the migration loop runs (`apply_schema_migrations`, `migrations.py:1011-1094`)
  — no TOCTOU gap once the lock is held; the only gap (pre-lock) is exactly what the revalidation
  is designed to catch.
- Every failure mode raises `MigrationBackupError`/`MigrationError` and rolls back — no
  logged-and-continue path.

### 5. Local AI configuration (Phase 4B) — PASS, no P1/P2 defects

- `local_ai/config.py::load_local_ai_configuration` is the single configuration authority;
  `slm/model_registry.py` and `slm/runtime_config.py` both call it and explicitly *raise* if a
  caller-supplied override doesn't match it, rather than silently accepting a divergent value.
- Role→model precedence is explicit (`_first()` chains in `config.py:97-111`,
  `LocalAIConfiguration.model_for_role()`).
- No silent model substitution: mismatches return a typed fallback (`used_real_slm: False`,
  `fallback_reason`), never a silent swap to a different real model.
- Inspection routes (`GET /runtime/local-ai/doctor`, `/capabilities`, `/hardware-ai/report`,
  `/runtime/context`) are read-only probes only — proven by a monkeypatch test that fails if
  `subprocess`/`Popen` is invoked during a probe call.
- Project synthesis defaults to disabled (`ASTRA_PROJECT_SYNTHESIS_MODE` default `"disabled"` →
  `UnavailableSynthesisGateway`, wired unconditionally at app startup) and `project_rag_enabled`
  is type-pinned `Literal[False]`.

### 6. Hardware authority (Phase 4B) — PASS, no P1/P2 defects

- `hardware_ai_optimizer` and `local_runtime/runtime_context.py` both delegate to
  `local_ai/hardware.py`'s canonical registry rather than running independent probes.
- Durable scheduler state overrides advisory readings: `_durable_gpu_busy()`
  (`local_ai/service.py:423-437`) queries live `local_ai_scheduler_jobs` and is consulted by
  `admission_preview`, proven by a test asserting a stale caller-supplied "GPU free" input is
  overridden by real claimed-job state.
- VRAM sizing for a single workload uses `max()` over devices, explicitly labeled
  `"largest_single_device"` — not a naive sum. (A separate `HardwareSnapshot.total_vram_bytes`
  convenience property *does* sum across devices, but production code only uses it as an
  existence check, never for capacity math — flagged as a P3 footgun below.)
- Partial/unavailable probe states are typed (`partial`, `errors`) and default to `None`/absent
  rather than a fabricated zero or generous default.

### 7. Frontend authority — PASS, one P1 and two P2 defects found; P1 + one P2 fixed

- The frontend consumes canonical backend read models as-is (`projectControlState.ts`,
  `ProjectControlCard.tsx`) with no local reconstruction of approval/completion state on the
  canonical path.
- No client-side lifecycle authority: `next_permitted_actions` filtering only hides stale
  buttons; the backend independently re-validates `expected_state_version` and all bindings on
  every mutation regardless of what the client renders.
- The one persisted UI-only setting sent to the backend (`safety_mode`) flows only into SLM
  prompt metadata — never into an `approval_required`/execution gate (`grep "safety_mode =="`
  over the backend returns nothing).
- **Defect (P1, fixed)**: Astra's own welcome-screen suggestion, `"Run the tests"`, deterministically
  produced a legacy `command`-type chat action (`backend/app/chat_actions.py`) whose only execution
  path is the now-retired `POST /assignments/commands/{id}/execute`. Every user who clicked
  Astra's own first-touch suggestion and approved it received a guaranteed 503. See "Defects
  found" below.
- **Defect (P2, fixed)**: the retired-route error body (`{"detail": {"code": ..., "message": ...}}`)
  was shown to users as raw unparsed JSON (`App.tsx::cleanError` just returned `error.message`
  verbatim) instead of the backend's human-readable `detail.message`.
- **Defect (P2, not fixed — documented)**: `/assignments/commands/{id}/approve` persists
  `status: "approved"` server-side before the immediately-following `execute()` call resolves
  (which now always 503s). A page reload or network interruption in that narrow window could
  leave a chat action durably showing "approved" with nothing to reconcile it. Narrow race,
  UX-only, not an authority or data-integrity issue — see Recommendations.

## Defects found (summary)

| # | Sev | Area | Defect | Status |
|---|-----|------|--------|--------|
| 1 | P1 | Frontend / R7 interaction | Welcome suggestion "Run the tests" deterministically routes into the retired `/assignments/commands/{id}/execute`, guaranteeing failure for a first-touch user action | **Fixed** |
| 2 | P2 | Manual verification (R6) | Dead `approve_manual_verification` action wiring reproduces the exact shape of an already-fixed P1 command-approval bypass; latent, currently unreachable, but a landmine for future changes | **Fixed** |
| 3 | P2 | Frontend | Retired-route (and general) error bodies shown as raw unparsed JSON instead of `detail.message` | **Fixed** |
| 4 | P2 | Frontend | Transient "approved" state can outlive a guaranteed-to-503 execute call across a reload/network interruption | Documented, not fixed (see Recommendations) |
| 5 | P2 | Phase 4B local AI | `POST /runtime/local-ai/roles/{role}` persists a role→model override that is never read back by actual model resolution — a misleading no-op 200 | Documented, not fixed (see Recommendations) |
| 6 | P3 | Migration (R8) | Failed/interrupted backup attempts accumulate orphaned `.bak` files with no pruning | Documented, not fixed |
| 7 | P3 | Migration (R8) | Non-empty-DB gate keys off main-file byte size; a WAL-only-committed near-empty main file is a narrow theoretical bypass of the backup gate | Documented, not fixed |
| 8 | P3 | Hardware (Phase 4B) | `HardwareSnapshot.total_vram_bytes`/`.free_vram_bytes` sum across GPUs and are unguarded against future misuse as a sizing value (currently only used as an existence check) | Documented, not fixed |
| 9 | P3 | Hardware (Phase 4B) | Duplicate legacy `psutil`-based memory probe (`backend/app/core/memory_monitor.py`) still exists, unimported by the canonical registry today | Documented, not fixed |
| 10 | P3 | R7 / main.py | Dead-code accumulation: 3 additional "unconditional raise, then unreachable legacy branch" sites beyond the 4 named R7 gates (`/chat/projects/commands/{id}/execute`, `/patches/{id}/apply`, `/rollback/{id}/approve`), all correctly deferring to the canonical worker on their live path | Documented, not fixed |

Items 4–10 are explicitly **not** authority bypasses, data-loss risks, or execution-boundary
violations — they were left as documented follow-ups rather than fixed in this pass to respect
the "smallest safe correction, no broad refactoring" constraint. None block Phase 5.

## Exact files changed in this pass

Backend:
- `backend/app/project_api/contracts.py` — removed `"approve_manual_verification"` from `CanonicalProjectActionDescriptor.action` Literal.
- `backend/app/project_api/routes.py` — removed the dead entry from `_next_actions`' `labels`/`artifact_types` dicts.
- `backend/app/project_control/project_service.py` — removed the dead `approve_manual_verification` branch from `command_types`, `required_artifact_types`, and both `elif` chains in `execute_action` (4 sites).

Frontend:
- `frontend/src/types/contracts.ts` — removed `"approve_manual_verification"` from `CanonicalProjectActionDescriptor.action` union.
- `frontend/src/state/projectControlState.ts` — removed it from `supportedActions`.
- `frontend/src/App.tsx` — removed `"Run the tests"` from the welcome suggestions; `cleanError` now routes through `describeAstraError`.
- `frontend/src/state/errorMessage.ts` (new) — pure `describeAstraError(raw)` helper that extracts `detail.message`/`detail` from a JSON error body, falling back to the raw text for anything else.

Tests:
- `tests/test_project_approval_binding.py` — added `test_approve_manual_verification_action_is_removed_and_fails_closed`.
- `frontend/tests/errorMessage.test.ts` (new) — 5 cases covering structured detail extraction, plain-string detail, non-JSON fallback, no-usable-detail fallback, and malformed-JSON fallback.

No changes were made to `project_workers/isolation.py`, `project_workers/execution.py`, or any
Docker isolation code. No package installs, model downloads, Docker pulls, training, or RAG
rebuilds occurred.

## Tests run and results

Focused suites (before fixes, to establish baseline) and again (after fixes, as the real gate):

```
$ TMP=/tmp TEMP=/tmp .venv/bin/python -m pytest -q \
  tests/test_project_approval_binding.py tests/test_project_compatibility.py \
  tests/test_project_manual_evidence.py tests/test_assignments_evidence.py tests/test_assignment_evidence_verification.py \
  tests/test_r7_legacy_execution_retirement.py tests/test_patch_apply.py tests/test_orchestrator.py \
  tests/test_chat_workflow.py tests/test_assignment_command_execution.py tests/test_assignment_execution_workflow.py \
  tests/test_database_migrations.py \
  tests/test_local_ai_phase4b.py tests/test_local_ai_stage7a.py tests/test_hardware_ai_optimizer \
  tests/test_project_coordinator_synthesis.py tests/test_project_synthesis_orchestrator.py tests/test_project_coordinator_execution.py \
  tests/test_project_api.py tests/test_project_control.py
325 passed in 104.76s   (post-fix; includes the 1 new regression test; +26 vs the 299 pre-fix baseline
                          from adding test_project_api.py/test_project_control.py to the post-fix run)

$ cd frontend && node --test --experimental-strip-types tests/*.test.ts
102 passed, 0 failed   (post-fix; 97 pre-fix + 5 new errorMessage.test.ts cases)

$ cd frontend && npm run build
tsc -b && vite build — clean, no errors

$ cd frontend && npm run lint
eslint . — clean, no errors

$ python -m compileall backend
clean

$ git --no-pager diff --check
clean
```

Full non-Docker backend suite (`pytest -q -m "not docker_integration"`), run as the final gate
after all fixes:

```
1161 passed, 8 skipped, 19 deselected in 205.69s (0:03:25)
```

`docker ps --filter name=astra-project-` — empty, no orphaned Astra containers left running.

## Blockers for Phase 5

**None.** All eight invariant categories pass; the one P1 and the P2s judged fixable within
"smallest safe correction" are fixed and covered by new regression tests; the full non-Docker
suite is green.

## Recommendations, ordered by severity

1. **P2** — Reconcile or remove the orphaned `/runtime/local-ai/roles/{role}` override endpoint
   (`local_ai/service.py:389-421`, `local_ai/routes.py:87-96`): it currently persists a role→model
   mapping that actual model resolution never reads back, returning a misleading success. Either
   wire it into `LocalAIConfiguration.model_for_role()` resolution or have it return a typed
   "not yet effective" response instead of a plain 200.
2. **P2** — Consider a server-side or reconciliation-side safeguard for the transient "approved"
   chat-action state that can now never successfully execute (`main.py:4011-4040`) — e.g. surface
   the retirement in the approve response itself, or have the read model reflect "approved,
   cannot execute" rather than a bare "approved" that silently fails on the next step.
3. **P3** — Prune orphaned `.bak` files from failed/interrupted migration-backup attempts
   (`database/migrations.py::_prepare_migration_backup`) to avoid disk exhaustion on a
   single-disk local deployment over repeated failures.
4. **P3** — Rename or remove `HardwareSnapshot.total_vram_bytes`/`.free_vram_bytes`
   (`local_ai/hardware.py:65-77`) to something like `aggregate_total_vram_bytes`, or delete them
   if truly unused, so a future caller can't accidentally use the summed value for capacity
   sizing and reintroduce the classic multi-GPU over-reporting bug.
5. **P3** — Delete the now-fully-superseded `backend/app/core/memory_monitor.py` (duplicate
   `psutil`-based probe, unimported by the canonical hardware registry) so it can't be
   accidentally wired back in as a second source of truth.
6. **P3** — When convenient, clean up the remaining dead "unconditional raise, then unreachable
   legacy branch" sites in `main.py` beyond the 4 R7-named routes (`/chat/projects/commands/{id}/execute`,
   `/patches/{id}/apply`, `/rollback/{id}/approve`) — functionally inert and correctly gated
   today, but each is 15-80 lines of dead code that makes future `main.py` diffs and audits
   larger than necessary.
7. **P3** — The non-empty-database gate for requiring a migration backup keys off main-file byte
   size only (`database/migrations.py` around the pre-migration check); confirm this can't be
   bypassed by a WAL-only-committed near-empty main file before relying on it in a context where
   an attacker controls file state between checkpoints.

None of the above are required before starting Phase 5; 1–2 are worth doing in the same
window since they touch UX honesty around the exact subsystems Phase 5 will exercise most
(local AI role selection, chat-driven command actions).
