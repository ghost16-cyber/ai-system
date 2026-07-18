# Stage 1: Canonical Project Control Plane

## Baseline and ownership map (before Stage 1)

The Stage 1 baseline was captured on `feature/chat-native-approval` before any
control-plane code changed. The focused backend regression set passed 192 tests
and the frontend suite passed 85 tests.

Lifecycle authority was distributed across these locations:

| Concern | Previous writers or decision makers |
| --- | --- |
| Delivery status | `project_delivery/service.py`, route-local copies in `main.py`, and `AnalysisRepository.transition_project_delivery_job` |
| Legacy job status | `project_jobs/workflow.py`, many route handlers in `main.py`, and `AnalysisRepository.transition_project_job` |
| Scope approval and launch | `client_engagement/service.py` and its independent engagement idempotency tables |
| Plan approval | `project_delivery/service.py::approve_plan`, stored inside mutable delivery JSON |
| Patch and command progress | patch/command routes in `main.py`, mirrored into both delivery and project-job JSON |
| Verification state | `project_delivery/verifier.py`, `project_delivery/service.py::record_verification`, and route-local command-result handling |
| Handoff eligibility | `project_delivery/service.py::generate_handoff`, `project_delivery/presentation.py`, and a separate calculation in `frontend/src/App.tsx` |
| Card creation and reload | delivery and project-job presentation helpers, conversation detail assembly in `repository.py`, and frontend merge/dedup functions |

The old records remain useful evidence and compatibility projections, but none
of them is a lifecycle authority after Stage 1. Their adapters submit commands
to the control plane and consume its read model.

## Ownership map (after Stage 1)

| Concern | Authoritative owner | Retained adapter boundary |
| --- | --- | --- |
| Lifecycle and terminal state | `ProjectRun.lifecycle_status` through `ProjectControlPlane.execute` | Stage 9 `DeliveryStatus` remains a compatibility projection |
| Legal transitions | `project_control/transitions.py` | Routes may request commands but cannot add transitions |
| Plan and scope definitions | `project_plan_revisions_v3` and `project_scope_revisions` | Stage 9 definitions are imported once as immutable evidence |
| Approval authority | `project_approval_grants` plus append-only invalidations | Legacy approval JSON is display/audit data and is never imported as authority |
| Work progress | `ProjectRun.work_unit_state` | Delivery and project-job runtime fields are presentation/execution bridges |
| Patch/command/verification attempts | `project_execution_attempts` | Patch, command and verifier modules perform bounded work only after a control-plane start command |
| Verification truth | current `ProjectRun.verification_state` accepted from fresh Stage 0 verifier results | Legacy verifier records supply typed evidence, not lifecycle decisions |
| Handoff eligibility | `ProjectControlPlane._validate_handoff` and the canonical read model | Legacy handoff assembly supplies report content only |
| Chat card and reload | `ProjectReadModel` embedded by `ProjectDeliveryControlAdapter` | Existing chat delivery card renders it; hidden execution-bridge job cards remain suppressed |

Delivery-linked project-job writes cannot advance canonical lifecycle state;
they only update the hidden execution projection. Client engagement can create a
project or request a scope revision, but those operations initialize or command
the same control plane. No legacy repository method writes any normalized
control-plane table.

## Canonical model

`ProjectRun` is the one mutable aggregate. It stores identity and bindings,
references to immutable records, runtime summaries, lifecycle status, terminal
metadata, and an optimistic `state_version`. It does not embed immutable plan or
scope definitions.

The immutable, versioned records are:

- `ScopeRevision`: specification-bound path and operation authority.
- `PlanRevision`: specification, scope, workspace, root, manifest, criteria,
  work-unit and limit bindings, identified by a canonical content hash.
- `ApprovalGrant`: exact authority bound to actor, conversation, workspace,
  root, plan, scope, specification, manifest and expected aggregate version.
- `ExecutionAttempt`: durable start and terminal result for a bounded action.
- `ProjectEvent`: append-only bounded metadata for every accepted mutation.

Definitions are content-addressed with canonical JSON (sorted keys, compact
separators, UTF-8). Runtime work-unit state is stored separately on the
aggregate and can never modify an approved definition.

## Mutation flow

Every accepted command follows one SQLite transaction:

1. Begin an immediate transaction and load the aggregate.
2. Validate schema, identity, actor and root bindings.
3. Validate expected state version and all supplied plan/scope/manifest IDs.
4. Resolve idempotency. An exact replay returns the stored typed result; a
   changed payload under the same key fails with `idempotency_conflict`.
5. Validate the command against the central transition matrix and approval
   requirements.
6. Insert immutable records or attempts, update the aggregate with optimistic
   concurrency, append one event, and store the deterministic result.
7. Commit atomically.

No event, attempt, or aggregate update survives a rejected command.

## Lifecycle transition matrix

| From | Legal destinations |
| --- | --- |
| `specification_pending` | `clarification_required`, `manifest_required`, `planning`, `blocked`, `cancelled` |
| `clarification_required` | `manifest_required`, `planning`, `blocked`, `cancelled` |
| `manifest_required` | `planning`, `blocked`, `cancelled` |
| `planning` | `awaiting_plan_approval`, `clarification_required`, `scope_change_required`, `blocked`, `cancelled` |
| `awaiting_plan_approval` | `ready_for_work`, `planning`, `scope_change_required`, `blocked`, `cancelled` |
| `ready_for_work` | `work_in_progress`, `verification_pending`, `planning` (explicit scope revision), `scope_change_required`, `blocked`, `cancelled` |
| `work_in_progress` | `awaiting_patch_approval`, `awaiting_command_approval`, `verification_pending`, `repair_required`, `planning` (explicit scope revision), `scope_change_required`, `rollback_pending`, `ready_for_work`, `blocked`, `cancelled` |
| `awaiting_patch_approval` | `work_in_progress`, `scope_change_required`, `blocked`, `cancelled` |
| `awaiting_command_approval` | `work_in_progress`, `verification_pending`, `repair_required`, `blocked`, `cancelled` |
| `verification_pending` | `ready_for_work`, `repair_required`, `scope_change_required`, `handoff_ready`, `blocked`, `cancelled` |
| `repair_required` | `work_in_progress`, `ready_for_work` (successful rollback), `planning` (explicit scope revision), `rollback_pending`, `scope_change_required`, `blocked`, `cancelled` |
| `scope_change_required` | `planning`, `blocked`, `cancelled` |
| `rollback_pending` | `ready_for_work`, `repair_required`, `blocked`, `cancelled` |
| `blocked` | `clarification_required`, `planning`, `repair_required`, `rollback_pending`, `cancelled` |
| `handoff_ready` | `handed_off`, `verification_pending`, `cancelled` |
| `handed_off` | `completed` |
| `cancelled`, `completed` | none |

Command-specific guards narrow this matrix. For example, `approve_plan` is the
only command that can enter `ready_for_work`, and it creates an approval grant;
status alone never implies approval.

## Identity, authority and invalidation

Commands bind project run, conversation, authorized workspace, canonical
repository-root fingerprint, actor, plan revision, scope revision, manifest,
expected version, exact authority, and idempotency identity. Superseding scope
or plan records invalidates active grants. Repository manifest changes make
manifest-bound authority and verification evidence stale. Migrated approval
objects are never imported as authority.

Plan approval remains bound to the plan's required starting manifest while an
authorized patch advances the live manifest through a recorded attempt. This
preserves the reviewed plan authority across its own expected changes without
treating an unrelated repository change as approved. Patch, command and
verification authority always bind the live manifest at that action boundary.

## Persistence, concurrency and recovery

The normalized SQLite schema uses foreign keys, checks, unique content and
idempotency constraints, deterministic event ordering, and indexes for project,
conversation, status, revision, request and event lookup. `BEGIN IMMEDIATE` plus
the aggregate version predicate makes simultaneous writers deterministic: one
wins and the loser leaves no partial records.

Active attempts survive restart as active records. Recovery may explicitly mark
an abandoned attempt interrupted; it never repeats execution automatically.
Unsupported schema versions and malformed stored JSON produce typed fail-closed
errors.

The normalized tables are:

| Table | Purpose and principal constraints |
| --- | --- |
| `project_runs` | one mutable aggregate row, schema/status/version columns and optimistic version predicate |
| `project_scope_revisions` | immutable, unique project revision number and content hash |
| `project_plan_revisions_v3` | immutable, unique project revision/hash with scope foreign key |
| `project_approval_grants` | immutable exact grant with plan/scope foreign keys and authority hash uniqueness |
| `project_approval_invalidations` | append-only one-to-one invalidation/supersession evidence |
| `project_execution_attempts` | unique project/type/idempotency and attempt-number records with durable active/terminal state |
| `project_events` | append-only project sequence and request uniqueness |
| `project_idempotency` | one deterministic request hash and typed result per project/key |
| `project_legacy_reconciliations` | one deterministic legacy-to-canonical identity mapping |

All connections enable foreign keys. Indexes cover conversation, workspace,
status, plan and scope revisions, approval bindings, active attempts, event
sequence/request, and idempotency request lookup.

Legacy delivery records are reconciled deterministically on first adapter read.
The migration imports identities and immutable evidence, invalidates all legacy
approval claims, requires re-approval, and appends one reconciliation event.
Conflicting or incomplete legacy evidence is blocked rather than guessed.

## Verification and handoff

Stage 0 verifier evidence remains authoritative evidence. The control plane
accepts it only when its result hash binds the current plan, scope, exact
criterion and complete live manifest. Manual criteria remain pending unless an
explicit manual authority record exists. Handoff eligibility is a backend-only
read-model field and requires current approvals, completed required work,
current patch/command grants, no unresolved scope or terminal block, complete
manifest, fresh automated results, explicit manual treatment, and a final live
manifest recheck.

## Chat-native read model and adapter boundary

The delivery card consumes the canonical read model: lifecycle, revisions,
manifest completeness, approval freshness, current work, progress, pending user
action, verification summary, block reason, handoff eligibility and state
version. Buttons send exact revision IDs, the expected version and a stable
idempotency key. Conflict responses force a refresh; the browser never infers a
successful transition.

`project_delivery`, `project_jobs`, client engagement and legacy chat endpoints
may still calculate domain evidence or compatibility payloads. They cannot
write `project_runs`, create canonical grants, advance canonical work state, or
decide canonical verification/handoff state except by submitting a command to
`ProjectControlPlane`.

## Known limitations and Stage 2 boundary

Stage 1 is a local SQLite control plane. It intentionally does not add worker
autonomy, distributed queues, container/VM/WSL isolation, mount or network
isolation, cloud execution, model-routing changes, unrestricted shell access,
or automatic approval. Execution remains in existing bounded Stage 0 adapters;
Stage 2 can replace those adapters without replacing lifecycle authority.

Legacy callers that predate Stage 1 may omit revision/version/idempotency fields
at the HTTP boundary. The server-side adapter fills those fields from the
canonical aggregate for compatibility; the shipped frontend always sends the
displayed project, plan, scope, version and a fresh stable idempotency identity.
Plan approval has full API-level deterministic replay; all control-plane
commands have deterministic engine-level replay.

## Verification record

The implementation was checked with focused transition/binding/idempotency/
concurrency/recovery/migration/handoff tests, the complete backend and frontend
suites, TypeScript, ESLint, a production build, and `git diff --check`. Manual
browser reproduction covered incomplete-manifest fail-closed behavior, one-card
canonical restoration after reload, exact plan approval, collapsed technical
details, and stale-action conflict refresh. No fixture file was mutated during
the browser reproduction.
