# Astra remaining implementation master plan

Status: audit and implementation plan after the Stage 2C checkpoint

Repository baseline: `feature/chat-native-approval` at `71bb81f` (`Add isolated Docker project runtime`)

Audit date: 2026-07-20

Scope: trustworthy local Python/Node coding-assistant core; no product implementation is included in this document

## 1. Executive summary

Astra has a strong trust and execution foundation. Durable chat identity, immutable approvals, the canonical `ProjectRun` lifecycle, transactional execution dispatch, exact host-side file mutation, and fail-closed Docker execution are implemented and tested. The system is not yet a complete local coding-assistant loop because the canonical control plane and the older project-delivery/project-job workflows still share authority in practice.

The highest-risk issue is not Docker. It is integration: a terminal worker result updates `ProjectRun`, but follow-on delivery projection, criterion processing, repair preparation, and handoff are still driven through legacy delivery/job records and explicit API calls. Cancellation also changes the canonical attempt without durably propagating cancellation to an already-enqueued worker request. The durable coordinator creates intents but no production component processes three of its four artifact-producing intent types.

The remaining work is organized into eight dependency-ordered stages:

1. Stage 3A — versioned migration and contract foundation.
2. Stage 3B — canonical project creation, artifacts, and read API.
3. Stage 3C — terminal reconciliation, cancellation propagation, and projections.
4. Stage 3D — coordinator artifact processor and work-unit loop.
5. Stage 3E — one-repair canonical orchestration.
6. Stage 3F — canonical chat card, API client, and reload.
7. Stage 3G — bounded synthesis/model evidence expansion.
8. Stage 3H — compatibility retirement, operations, and MVP gates.

Stages 3A–3F are required for Astra's reliable local coding-assistant core. Stage 3G is useful after that core is coherent; only its bounded multi-file evidence and idempotent model-call portions are MVP requirements. Project RAG, additional providers, and further languages are optional future expansion. Cloud/distributed execution, multi-agent orchestration, automatic approvals, request-time dependency installation, arbitrary user images, runtime networking, host project-code execution, and writable real-repository mounts are explicitly rejected or deferred.

## 2. Verified baseline

The code and tests verify the following baseline. Test totals are taken from the checked-in Stage 2C stabilization record; this planning audit did not rerun broad suites.

| Area | Active implementation | Verification evidence |
|---|---|---|
| Durable chat intake | `ChatRunRequest`, `ChatRequestRecord`, `AnalysisRepository.create_chat_request()`, `claim_chat_request()`, `/chat/requests`, `/chat/stream` | `tests/test_project_delivery.py::test_pending_request_is_durable_before_stream_and_duplicate_stream_does_not_reexecute`; `frontend/tests/conversationReloadState.test.ts` |
| Folder authority | `backend/app/folders/actions.py`, `backend/app/folders/scanner.py`, `backend/app/folders/safety.py`, `backend/app/folders/reader.py`; folder request/approve/cancel/rescan routes | `tests/test_folder_scanner.py`, `tests/test_folder_reader_search.py`, `tests/test_project_workspace_chat.py` |
| Canonical lifecycle | `ProjectControlPlane`, `ProjectRun`, `ProjectCommand`, `LEGAL_TRANSITIONS`, optimistic versions, idempotency, append-only events | `tests/test_project_control.py` |
| Immutable scope/plan/approval | `ScopeRevision`, `PlanRevision`, `ApprovalGrant`; approval invalidation and binding validation | `tests/test_project_control.py`; `tests/test_project_delivery.py` |
| Durable dispatch/queue | `ExecutionDispatch`, `ProjectWorkerQueue`, `ProjectWorkerService.dispatch_pending()` and lease/reconciliation logic | `tests/test_project_worker_dispatch.py`, `tests/test_project_workers.py` |
| Exact mutations | `FileMutationSpec`, `FileMutationEngine`, durable journals and snapshots | `tests/test_project_file_mutations.py`, `tests/test_project_mutation_worker.py` |
| Docker isolation | `IsolationBackend`, `DockerIsolationBackend`, `ProjectIsolatedExecutor`, pinned runtime image and snapshot exclusions | 19 Docker integration tests in `tests/test_project_worker_docker_integration.py`; unit coverage in `tests/test_project_worker_isolation.py` and `tests/test_project_worker_isolated_execution.py` |
| Runtime capability | `/chat/projects/runtime-capabilities`, worker heartbeats, Docker probe | `tests/test_project_worker_runtime.py`; real endpoint coverage in `tests/test_project_worker_docker_integration.py` |
| Structural analysis/synthesis | `build_project_index()`, `build_analysis_plan()`, deterministic synthesis, strict model synthesis and prevalidation | `tests/test_project_analysis.py`, `tests/test_model_synthesis.py` |
| Diagnosis/repair evidence | bounded failure evidence, deterministic/model diagnosis, legacy repair-cycle records | `tests/test_project_diagnosis.py` |
| Delivery/handoff | deterministic specification/plan/verifier/handoff services and chat presentation | `tests/test_project_delivery.py`; `frontend/tests/projectDeliveryStage9.test.ts` |
| Client scoping | `EngagementService`, immutable scope revision/approval, launch bridge | `tests/test_client_engagement.py`; `frontend/tests/clientEngagementStage10.test.ts` |

Recorded Stage 2C gates: 946 backend tests, 19 real Docker tests, 87 frontend tests, ESLint, and the production frontend build passed. The isolation checkpoint also records no managed containers or lingering backend/worker/Vite/pytest processes.

Non-negotiable preserved invariants:

- The backend issues conversation, request, project, attempt, dispatch, and worker identities.
- Conversation and pending request exist durably before streaming begins.
- `ProjectControlPlane.execute()` is the only lifecycle mutation authority.
- Revisions and grants are immutable; every authority is bound to exact plan, scope, manifest, and artifact hashes.
- An attempt and its dispatch outbox row are committed together before execution.
- Reload/retry returns existing durable identity and never implicitly reruns model or worker work.
- Restart recovery interrupts or resumes reconciliation; it never silently re-executes an attempt.
- Patch, rollback, command, and manual verification approvals remain separate.
- Model output is advisory until strict parsing, binding, scope, manifest, and virtual-file validation pass.
- Project code executes only in the pinned, networkless Docker snapshot; containers never write the real repository.
- Missing capability, evidence corruption, stale state, cleanup failure, or unsupported toolchain fails closed.

## 3. Active architecture map

`AnalysisRepository` is the general SQLite repository. Canonical control, worker queue, mutation engine, and coordinator each use their own normalized tables in the same database. `backend/app/main.py:create_app()` constructs all of them. The separate execution loop is `python -m backend.app.project_workers`; FastAPI initializes and reports state but does not execute the queue.

The following map names the active route, schemas, service/control entry, persistence, emitted canonical event or legacy audit, frontend consumer, and primary specification tests. Canonical `ProjectEvent.event_type` is the exact `ProjectCommandType.value` submitted to `ProjectControlPlane.execute()`; legacy routes also append their named operation to `project_delivery_audit_events` or `project_audit_events`. “Legacy projection” means it is active for current UI compatibility but must not remain lifecycle authority.

| Workflow | API and schema | Active backend path | Persistence/events | Frontend and tests |
|---|---|---|---|---|
| Chat request intake | `POST /chat/requests`, `POST /chat/stream`; `ChatRunRequest` → `ChatRequestRecord`/NDJSON `ChatRunResponse` | `_create_pending_chat_request()` → `AnalysisRepository.create_chat_request()` → `claim_chat_request()`; `/chat/stream` validates request/conversation/message binding before dispatch | `chat_conversations`, `chat_requests`, `chat_runs`; request status is durable, not a browser flag | `AstraClient.createChatRequest()`, `streamChat()`; `frontend/src/state/conversationReloadState.ts`, `frontend/src/state/chatStreamState.ts`, `frontend/src/App.tsx`; delivery reload tests at lines 709+ in `tests/test_project_delivery.py` |
| Folder binding | `POST /chat/folders/request`; approve/cancel/rescan routes; `ChatFolderRequest`, `ChatFolderActionRequest` | `create_folder_chat_run()`, scanner and safety functions; `_completed_project_access()` revalidates completed action and root fingerprint | authority is persisted in `chat_runs.action`; folder audit in `project_audit_events` | `AstraClient.requestChatFolder()`, `approveChatFolder()`, `cancelChatFolder()`, `rescanChatFolder()`; `frontend/src/state/folderAccessState.ts`; folder card in `frontend/src/App.tsx`; folder/project-workspace tests |
| Project creation | `POST /chat/projects/deliveries`; `ProjectDeliveryStartRequest` | `_start_project_delivery()` → `create_delivery_job()` → `AnalysisRepository.store_project_delivery_job()` → `ProjectDeliveryControlAdapter.decorate()/ensure()` → `ProjectControlPlane.reconcile_legacy_delivery()` | legacy `project_delivery_jobs` plus canonical `project_runs`, scope/plan revisions, events and reconciliation mapping | `frontend/src/state/projectDeliveryState.ts`, `ProjectDeliveryCard`; `tests/test_project_delivery.py` |
| Client-scope creation/revision | `/chat/client-engagements*`; `Engagement*Request` | `EngagementService` methods; launch calls Stage 9 delivery creation; later delivery scope revision calls `revise_delivery_scope()` then adapter | `client_engagement*` tables and audit; launch references delivery; canonical scope is created/revised through adapter | `frontend/src/state/clientEngagementState.ts`, engagement card; `tests/test_client_engagement.py` |
| Plan creation/revision | delivery start, clarify, `POST .../scope-revision` | `build_execution_plan()`, `revise_delivery_scope()`; adapter `_propose_plan()` executes `PROPOSE_PLAN_REVISION` | `project_delivery_plan_revisions` compatibility records plus `project_plan_revisions_v3`; `project_events` | `ProjectDeliveryAction.plan`; Stage 9 frontend/backend tests |
| Plan approval | `POST .../plan/approve`; `ProjectDeliveryHashRequest` | `ProjectDeliveryControlAdapter.approve_plan_bound()` executes `APPROVE_PLAN`; legacy `approve_delivery_plan()` is then saved as projection | canonical `project_approval_grants`, `project_events`, `project_idempotency`; compatibility approval in delivery JSON | `exactPlanApprovalRequest()`, `approveDeliveryPlan()`; exact/stale/concurrency tests |
| Patch preparation | `POST .../prepare`; `ProjectJobActionRequest` | `activate_next_work_unit()` → hidden legacy `project_job` → `prepare_job_patch_bundle()` → `create_patch_proposal()` → `link_patch_preview()`; adapter executes `BEGIN_WORK_UNIT` and `RECORD_PATCH_PREVIEW` | `project_jobs`, `project_patches`, `project_synthesis_attempts`, delivery records/audit plus canonical event/attempt | `prepareProjectDelivery()` adds patch action and refreshes card; delivery/model-synthesis tests |
| Patch approval/application | `POST /chat/projects/patches/{id}/approve` and `/apply`; `ProjectPatchApprovalRequest`, `ProjectPatchApplyRequest` | exact legacy validation; adapter `APPROVE_PATCH`; canonical delivery path builds `FileMutationSpec`, executes `BEGIN_PATCH_APPLICATION` with dispatch; `ProjectMutationExecutor` → `FileMutationEngine.apply()` | approval/attempt/dispatch in canonical tables; worker queue; mutation spec/journal/snapshot; patch/delivery compatibility records | generic patch card plus delivery refresh in `frontend/src/App.tsx`; mutation, dispatch, workspace-chat and Docker tests |
| Command approval/execution | `/chat/projects/commands/{id}/approve`, `/execute`, `/cancel`; assignment command schemas | assignment command store validates the exact command/token; delivery adapter executes `APPROVE_COMMAND` and `BEGIN_COMMAND_EXECUTION`; queue → `ProjectIsolatedExecutor` → Docker | assignment command files plus canonical approval/attempt/dispatch, worker tables, result evidence file/reference | `AstraClient.approveProjectCommand()`, `executeProjectCommand()`, `cancelProjectCommand()`; `frontend/src/state/chatCommandState.ts`; worker execution and command frontend tests |
| Worker dispatch | no mutating public worker route; runtime capability is read-only | `ProjectControlPlane.execute()` creates `ExecutionAttempt` and `ExecutionDispatch` transactionally; `ProjectWorkerService.dispatch_pending()` enqueues idempotently and marks delivered | `project_execution_attempts`, `project_execution_dispatches`, `project_worker_requests`, `project_worker_idempotency`, `project_worker_events` | delivery canonical execution fields; dispatch/worker tests |
| Docker execution | separate `python -m backend.app.project_workers` | `build_runtime()` → `CompositeProjectExecutor` → mutation or `ProjectIsolatedExecutor`; `DockerIsolationBackend.probe()/execute()/cleanup_orphans()` | runtime heartbeat, worker result/reference, isolation evidence file, queue events | runtime endpoint/card; unit and real Docker tests |
| Terminal result ingestion | internal worker reconciliation | `ProjectWorkerService._reconcile_terminal()` → `_reconcile_result()` for PATCH/ROLLBACK/COMMAND/VERIFICATION or `_recover_canonical_attempt()` | canonical `RECORD_*`/`RECOVER_ATTEMPT` event, attempt state, worker `canonical_reconciled_at` | canonical fields appear on next delivery GET; worker tests |
| Deterministic verification | `POST .../verification`; `ProjectDeliveryVerificationRequest` | `run_deterministic_verifier()` → legacy `record_delivery_verification()` → adapter `_record_verification()` executes canonical commands | `project_verifier_results`, delivery verification records plus `ProjectRun.verification_state`/attempt/event | `verifyProjectDelivery()` and criterion card; delivery verifier tests |
| Subprocess verification | verification route proposes command; command approve/execute routes | approved execution is canonical/Docker for delivery-bound work; worker result can execute `RECORD_VERIFIER_RESULT` only when criterion bindings are in execution payload | canonical command/verification attempt and evidence plus legacy command/delivery/job records | command card then delivery refresh; worker execution and delivery tests |
| Diagnosis/repair | job failure/diagnosis endpoints and chat repair follow-up | legacy command-result branch builds failure evidence/repair cycle; `diagnose_project_failure()` and `prepare_job_patch_bundle()` create repair patch; adapter maps repair preview to `RECORD_PATCH_PREVIEW` | `project_failure_evidence`, `project_diagnoses`, `project_repair_cycles`, `project_jobs`, `project_patches`; canonical repair attempt is only partially represented | `frontend/src/state/projectJobState.ts`, delivery `stage8`, `frontend/tests/projectRepairStage8.test.ts`; diagnosis tests |
| Cancellation | delivery/job/command routes | delivery adapter executes `CANCEL_PROJECT`; control cancels active attempts and pending dispatches; worker queue has independent `request_cancel()` but the delivery route does not call it | canonical attempt/dispatch and delivery/job state; queued/running worker request may remain independently active | cancel buttons in `frontend/src/App.tsx`; unit cancellation tests, but missing API-to-running-worker test |
| Rollback | `/chat/projects/rollback/request`, `/{patch}/approve|reject` | adapter records/approves/begins rollback; `FileMutationEngine.build_rollback_operations()` and mutation worker apply exact snapshot | canonical grant/attempt/dispatch, mutation journal/snapshot, patch/delivery projections | patch action card and delivery refresh; mutation/delivery tests |
| Reload recovery | `GET /chat/conversations/{id}`; `ChatConversationDetail` | repository returns turns/requests; route separately loads legacy jobs and decorated deliveries (which creates/reconciles canonical data on read) | multiple stores are merged; active chat requests may be marked interrupted on backend restart | `restoreConversationMessages()` in `frontend/src/App.tsx` merges turns, jobs and deliveries; `frontend/src/state/conversationReloadState.ts`; reload tests |
| Handoff | `POST .../handoff`; `ProjectJobActionRequest` | legacy `generate_handoff()` performs checks; adapter executes `REQUEST_HANDOFF` and two `FINALIZE_PROJECT` commands | legacy handoff in delivery JSON and canonical handoff eligibility/lifecycle/attempt | `generateDeliveryHandoff()`, handoff section in card; delivery/control tests |
| Legacy reconciliation | implicit in decorated delivery reads and operations | `ProjectDeliveryControlAdapter.ensure()` → `ProjectControlPlane.reconcile_legacy_delivery()` | `project_legacy_reconciliations`; legacy approval is discarded and reapproval required | historical cards use `legacyLifecycle()` only without canonical control; legacy tests in control/delivery suites |

Active persistence/event/frontend detail:

| Path group | Repository/control methods and tables | Canonical event(s) or legacy audit | Client → state → rendered card |
|---|---|---|---|
| Folder/chat | `AnalysisRepository.store_chat_run()`, `get_chat_conversation()`, `list_chat_requests_for_conversation()`; `chat_conversations`, `chat_requests`, `chat_runs` | folder/project operations use `audit_event()` → `project_audit_events`; chat request status is not a project event | `HttpAstraClient` methods → `folderAccessActionFromPayload()`/`restoreConversationMessages()` → folder/generic chat cards in `frontend/src/App.tsx` |
| Engagement/scope | `store_client_engagement()`, `transition_client_engagement()`, `store_client_engagement_records()`, engagement idempotency/audit methods; all `client_engagement*` tables | engagement-specific audit operations; launch/scope bridge later emits `initialize_project`, `attach_specification`, `register_manifest`, `propose_plan_revision` | engagement client methods → `clientEngagementActionFromPayload()` → engagement card in `frontend/src/App.tsx` |
| Delivery/plan | `store_project_delivery_job()`, `transition_project_delivery_job()`, `store_project_delivery_record()`, `store_project_delivery_audit_event()`; delivery tables plus canonical control tables | `initialize_project`, `attach_specification`, `register_manifest`, `propose_plan_revision`, `approve_plan`, `revise_scope`; named delivery audit operations | delivery client methods → `projectDeliveryActionFromPayload()` → `ProjectDeliveryCard` |
| Patch/rollback | `store_project_patch()`, `transition_project_patch()`, `update_project_patch()`; mutation/control/worker stores | `record_patch_preview`, `approve_patch`, `begin_patch_application`, `record_patch_result`; rollback equivalents | patch/rollback client methods → `actionFromPayload()` plus delivery refresh → generic patch card and `ProjectDeliveryCard` |
| Command/verification | assignment command store plus delivery/control/worker persistence; `store_project_failure_evidence()` on legacy failure | `record_command_preview`, `approve_command`, `begin_command_execution`, `record_command_result`, `request_verification`, `record_verifier_result` | command client methods → `chatCommandState.ts` and delivery parser → command card plus `ProjectDeliveryCard` |
| Diagnosis/repair | `store_project_failure_evidence()`, `store_project_diagnosis()`, `store_project_repair_cycle()`, `store_project_synthesis_attempt()`; corresponding legacy tables | legacy repair audit operations; canonical `initiate_repair`/`record_patch_preview` only when adapter is invoked | chat/job endpoints → `projectJobActionFromPayload()` and delivery `stage8` → hidden bridge/job evidence and delivery card |
| Worker/runtime | `ProjectControlPlane.list_pending_execution_dispatches()/mark_execution_dispatch_dispatched()`, `ProjectWorkerQueue.enqueue()/claim_next()/complete()/mark_canonical_reconciled()` | worker terminal ingestion emits one of the canonical `record_*` or `recover_attempt` events; queue also appends `project_worker_events` | runtime/delivery GET → delivery parser execution fields → isolated-execution section |
| Reload/handoff | conversation list methods plus `list_project_jobs_for_conversation()`/`list_project_delivery_jobs_for_conversation()`; handoff saved through delivery transition and control | `request_handoff`, then two `finalize_project` events for the current adapter | `getChatConversation()` → `restoreConversationMessages()` → one deduplicated card per legacy job/delivery ID |

## 4. Canonical source-of-truth map

| Concern | Canonical authority now | Allowed projection/compatibility | Rule after this plan |
|---|---|---|---|
| Conversation/request identity | `chat_conversations`, `chat_requests`; backend-issued IDs | browser recovery marker is display-only | unchanged |
| Project lifecycle and next action | `ProjectRun` mutated only by `ProjectControlPlane.execute()` | delivery/job status may describe historical records | all new UI/API actions derive from `ProjectReadModel` only |
| Legal transitions | `LEGAL_TRANSITIONS`, `COMMAND_SOURCES`, `validate_transition()` | none | add matrix tests before extending commands |
| Scope and plan | `ScopeRevision`, `PlanRevision` | engagement/delivery records are source evidence during migration | new projects store canonical artifacts/revisions first; compatibility becomes projection |
| Approval | `ApprovalGrant` plus invalidations | assignment command token and legacy approval are preflight compatibility only | one canonical approval validator per authority; legacy token cannot expand authority |
| Attempt/dispatch | `ExecutionAttempt`, `ExecutionDispatch` | worker request mirrors the attempt | no route may invent or infer an attempt |
| Worker result | canonical `RECORD_*` or `RECOVER_ATTEMPT` command | queue terminal row is evidence, never lifecycle authority | reconciliation must also emit durable projection/coordinator work without rerun |
| File state | fresh `ProjectStateManifest`, `FileMutationSpec`, mutation journal/snapshot | `project_patches` is preview/history | all real-repository writes go through `FileMutationEngine` |
| Verification | criterion-bound canonical verifier result and `ProjectRun.verification_state` | legacy verifier record is historical evidence/projection | canonical artifact is the only current result; stale evidence is never promoted |
| Repair budget/state | not yet fully canonical | legacy job/repair-cycle state currently active | Stage 3E adds canonical repair-cycle artifact/budget and makes legacy read-only |
| Handoff eligibility | `ProjectControlPlane._validate_handoff()` and `_read_model()` | legacy handoff report is presentation | handoff artifact must be bound before lifecycle can become handed off/completed |
| Coordinator work | `project_coordinator_intents` is durable intent authority | none | processors may create artifacts and submit commands, never edit `ProjectRun` directly |

## 5. Legacy and compatibility inventory

| Item | Classification | Evidence and required disposition |
|---|---|---|
| `ProjectDeliveryControlAdapter` | active compatibility boundary and migration target | Correctly funnels legacy transitions through control, but `apply_transition()` derives canonical commands from already-mutated legacy objects. Retain only for historical import/read after Stage 3F. |
| `project_delivery_jobs` and normalized delivery record tables | active compatibility projection and unresolved risk | New projects are created here before reconciliation. Convert to read projection/history in Stages 3B–3C. |
| `project_jobs` hidden Stage 9 bridge | active compatibility-only, removal target | `frontend/src/state/projectJobState.ts` deliberately hides delivery bridge jobs. Replace artifact preparation and repair orchestration, then stop creating bridge jobs. |
| `project_patches` | active preview/history store and migration target | Exact approvals currently bind a legacy patch ID. Import previews into canonical `ProjectArtifact`; retain historical read support. |
| assignment command store | active correct for older assignment workflow; duplicate authority for canonical project commands | Canonical project execution currently validates both assignment token and `ApprovalGrant`. Keep assignment endpoints, but canonical project commands need a canonical command artifact/approval path. |
| direct `apply_project_patch()` / `rollback_project_patch()` branches | historical compatibility and security removal target | `/chat/projects/patches/{id}/apply` and rollback can write on host when no delivery relation exists. Disable mutation authority after migration; never use for new canonical work. |
| `ProjectSubprocessExecutor` and `ASTRA_ALLOW_LEGACY_PROJECT_EXECUTION=1` | explicit compatibility/removal target | Guarded but still capable of host project-code execution. Remove in Stage 3H after historical recovery conversion. |
| legacy host command execution in `/chat/projects/commands/{id}/execute` | active for non-delivery project jobs; removal target | Canonical delivery commands queue Docker work, but old job commands can still call `execute_assignment_command()`. Migrate/reject project-root execution. |
| `legacyLifecycle()` in `frontend/src/state/projectDeliveryState.ts` | correct historical read fallback, later isolate/remove from new path | Must be reachable only for records tagged historical/compatibility. |
| `docs/FINAL_SYSTEM_STATUS.md` | stale documentation | Calls an earlier deterministic foundation the current runtime. Replace with a pointer or archive marker in Stage 3H. |
| `docs/MIGRATION.md`, cleanup/duplicate reports | historical documentation | Already partly marked historical. Keep as archive, remove “current” cross-links. |
| old assignment/RAG/training APIs | adjacent active product capabilities, not project lifecycle authority | Do not rewrite as part of core. Project RAG remains opt-in future evidence only. |

## 6. Competing-authority findings

1. **Legacy delivery mutation precedes or surrounds canonical mutation.** `_save_delivery_transition()` calls the adapter unless `canonical_preapplied`, then writes `project_delivery_jobs`. Several paths build the next state with `project_delivery.service` and ask the adapter to infer the canonical command. Classification: migration target; High integrity risk.
2. **Worker completion and delivery projection are separate.** `ProjectWorkerService._reconcile_result()` changes only canonical state. `_validated_project_delivery()` later calls `_reconcile_completed_delivery_mutation()` for patch/rollback, while command/verification follow-on remains explicit. Classification: unresolved High workflow risk.
3. **Verification exists twice.** `project_verifier_results`/delivery records and `ProjectRun.verification_state` each represent current results. The adapter selects the last legacy result and recreates a canonical result. Classification: unresolved Critical authority risk for completion/handoff.
4. **Handoff exists twice and is ordered backwards for the target architecture.** `generate_handoff()` creates the legacy report, then the adapter requests/finalizes canonical handoff in three commands. Canonical `REQUEST_HANDOFF` currently creates a terminal-success attempt before a first-class handoff artifact is stored. Classification: High migration target.
5. **Frontend lifecycle is partly inferred.** `projectDeliveryActionFromPayload()` prefers `project_control`, but falls back to delivery status and `legacyLifecycle()`. Reload merges turns, jobs, and deliveries. Classification: correct for history, unresolved risk if new records lack canonical fields.
6. **Approval checks are duplicated.** Patch approval runs `verify_patch_approval()` and canonical approval; command approval uses assignment command approval token and canonical grant. Classification: active compatibility, Medium maintainability risk and High risk if bindings diverge.
7. **Idempotency is duplicated.** Canonical `project_idempotency`, worker idempotency, engagement idempotency, request claiming, delivery optimistic writes, and route-specific fixed keys are individually useful, but artifact/model intent does not have one end-to-end identity. Classification: High integration gap.
8. **Worker terminal decisions are appropriately constrained.** Queue rows never write `ProjectRun`; worker service submits typed canonical commands. Classification: active and correct. Preserve this boundary.
9. **Coordinator correctly avoids lifecycle writes.** `ProjectCoordinatorService` only creates/leases/completes intents. Classification: active and correct infrastructure, but incomplete without processors.
10. **Repository methods directly write legacy statuses.** `transition_project_delivery_job()`, `transition_project_job()`, `transition_project_patch()`, and update methods remain active. Classification: compatibility/migration targets, not acceptable as new-project authority.
11. **Cancellation authority is split.** `CANCEL_PROJECT` cancels canonical attempts/pending dispatches; `ProjectWorkerQueue.request_cancel()` is separate and not called by the delivery cancel route. Classification: unresolved Critical execution-control risk.
12. **GET can mutate through reconciliation/projection.** `_validated_project_delivery()` can adapt/store a legacy plan, ensure canonical state, reconcile completed mutation, and create coordinator intent. Classification: High design risk; canonical reads should be side-effect-free after an explicit migration checkpoint.

## 7. Remaining gap register

| ID | Severity | Gap and current → expected behavior | Evidence | Dependencies / resolution / tests / acceptance |
|---|---|---|---|---|
| ASTRA-G001 | Critical | **Split lifecycle authority.** New delivery/job state is mutated alongside canonical state → all new lifecycle changes originate as canonical commands; legacy data is projection/history. | `backend/app/main.py:_save_delivery_transition`; `backend/app/project_control/adapters.py:ProjectDeliveryControlAdapter.apply_transition`; `backend/app/project_delivery/service.py` | 3A–3C. Add ownership tests that fail on direct new-project status writes. Accept when every new-project transition has one canonical event and projections cannot drive it. |
| ASTRA-G002 | High | **Terminal result does not advance the full workflow.** Worker result updates attempt/lifecycle, while delivery criteria/card/follow-on require read reconciliation or clicks → one idempotent terminal ingestion produces canonical result artifact, projection, and next coordinator intent. | `ProjectWorkerService._reconcile_result()`; `_reconcile_completed_delivery_mutation()`; Stage 2C docs | G001, 3B–3C. Test crash after queue completion, after canonical command, after projection, and after coordinator creation: one result/intent/model call. |
| ASTRA-G003 | Critical | **Cancellation is not propagated to an enqueued/running worker.** Canonical attempt can be cancelled while queue request remains active → cancellation outbox reaches the exact worker request and container before canonical terminalization. | `ProjectControlPlane._cancel_active_attempts()`; `ProjectWorkerService.request_cancel()`; delivery cancel route | 3A, 3C. API + real Docker cancellation tests. Accept only when running process group/container stops, mutation commit boundary is honored, and states converge. |
| ASTRA-G004 | High | **Coordinator intents have no production processors.** Durable intents exist for work unit, deterministic verification, repair, handoff → separate worker loop claims, builds immutable artifacts, submits a canonical command, then completes the intent. | `ProjectCoordinatorService`; `backend/app/project_workers/__main__.py:worker_cycle()` supported types | 3B–3D. Lease/restart/idempotency/model-call tests. One intent may create one artifact and one command only. |
| ASTRA-G005 | Critical | **Verification has dual current records.** Legacy verifier data is converted into `ProjectRun.verification_state` → immutable canonical verifier artifact is authoritative; delivery records project it. | `backend/app/project_delivery/verifier.py`; `ProjectDeliveryControlAdapter._record_verification()`; `ProjectControlPlane._validate_verifier_result()` | 3B–3D. Bind criterion/plan/scope/manifest/execution/toolchain/evidence hashes; stale or mismatched results fail closed. |
| ASTRA-G006 | High | **Repair bypasses the canonical durable loop.** Legacy job failure creates repair records and model synthesis; canonical repair attempt is only a shell state → one bounded diagnosis and repair artifact per failure, exact repair approval, queued mutation, fresh verification, no automatic second repair. | legacy command-result branch in `backend/app/main.py`; `backend/app/project_analysis/diagnosis`; `ProjectCommandType.INITIATE_REPAIR` | 3D–3E. Full failed verification → preview → approval → verify tests with reload at each edge. |
| ASTRA-G007 | Critical | **Historical host execution/mutation authority remains callable.** Non-delivery patch/rollback/command paths can affect/run a connected repository on host → historical records are read/recovery-only; project-root work migrates or returns typed blocked response. | patch/rollback/command branches in `backend/app/main.py`; `ProjectSubprocessExecutor` | 3C, 3H. Negative tests assert no host process/write. Remove env opt-in and calls after compatibility gate. |
| ASTRA-G008 | Critical | **No versioned database migration runner.** Services use independent `CREATE IF NOT EXISTS` and ad-hoc `ALTER TABLE` → ordered transactional migrations with checksums, compatibility window, backup/restore guidance, and startup refusal on unknown/newer schema. | repository `_ensure_columns`; control `initialize()`; coordinator/queue/mutation `initialize()` | First: 3A. Upgrade copies of Stage 0/1/2A/2B/2C DBs; interrupt every migration; exact rerun; downgrade/backup test. |
| ASTRA-G009 | High | **Artifacts are not canonical records.** Patch preview, command plan, evidence pack, diagnosis, verifier result, and handoff are spread across JSON/files/tables → immutable typed `ProjectArtifact` records linked to events, revisions, attempt and content hash. | `project_patches`, synthesis attempts, result-reference files, delivery handoff | 3A–3B. Corruption/hash/reference tests; no lifecycle transition can cite a missing/mismatched artifact. |
| ASTRA-G010 | High | **Canonical project creation begins from legacy delivery.** Read decoration imports it into control → create `ProjectRun`, scope, manifest, plan/artifacts transactionally through explicit service; project-delivery becomes projection. | `_start_project_delivery()`; adapter `ensure()`/`reconcile_legacy_delivery()` | 3B. Refresh immediately during creation yields one project and no read-side mutation. |
| ASTRA-G011 | High | **Frontend reload consumes multiple authorities.** Runs, requests, jobs, and deliveries are merged; lifecycle fallback exists → canonical project collection/read models are delivered in hydration and reducer never infers new lifecycle. | `ChatConversationDetail`; `restoreConversationMessages()`; `legacyLifecycle()` | 3B, 3F. Reload at every attempt/intent state; canonical card identity once; temporary lookup errors retain project. |
| ASTRA-G012 | Medium | **Canonical project API is weakly typed in the frontend.** Delivery methods use `Record<string, unknown>` and combined legacy payload → versioned backend response and TypeScript contracts. | `frontend/src/clients/astraClient.ts`; delivery GET routes | 3B, 3F. Contract fixtures and schema-version rejection tests. |
| ASTRA-G013 | High | **Model calls lack canonical invocation identity/budget.** Synthesis attempts are legacy-job scoped; coordinator retry could call Ollama again → durable invocation keyed by intent/artifact/revision/evidence hash with lease and terminal result. | `backend/app/project_analysis/model_synthesis/gateway.py`; `project_synthesis_attempts`; coordinator intents | 3A, 3D, 3G. Crash before/after provider response and reload tests prove one invocation. |
| ASTRA-G014 | Medium | **Evidence/output retention and operator visibility are incomplete.** Evidence is bounded/redacted but retention/GC and aggregate recovery diagnostics are not explicit → content hashes, classifications, expiry policy, safe GC, reconciliation counters, last-error endpoint. | isolated/subprocess evidence writers; runtime endpoint | 3G–3H. Redaction/retention/corrupt-reference tests and operator manual runbook. |
| ASTRA-G015 | Medium | **Dependency/toolchain compatibility is discovered late.** Runtime image is fixed and installation prohibited → preflight profile records required commands/manifests and returns typed `unsupported_dependency` before approval where determinable. | isolation policy/profile; Stage 2C limitations | 3G. Python/Node supported smoke plus absent dependency/language typed block; never install or network. |
| ASTRA-G016 | Medium | **Transition matrix and recovery combinations are incomplete.** Core edges are tested, but not every legal/illegal command source, approval invalidation, reconciliation crash point, or cancellation edge → generated exhaustive matrix and stateful recovery tests. | `backend/app/project_control/transitions.py`; current focused tests | All stages, gate in 3H. Every command/source pair asserted; invariant/property tests retain one attempt/result. |
| ASTRA-G017 | Medium | **Packaging/startup remains manual and three-state.** Backend, worker, Docker image/env and frontend require manual coordination → doctor/start scripts report—not repair—DB/runtime/worker/image state and provide deterministic commands. | README worker section, build/load scripts, runtime endpoint | 3H. Clean-machine WSL2 reproduction, no implicit build/pull/install. |
| ASTRA-G018 | Low | **Documentation labels conflict.** `docs/FINAL_SYSTEM_STATUS.md` describes an earlier system as current → one active status/runbook and clearly historical documents. | docs inventory | 3H. Link check and operator review. |
| ASTRA-G019 | Medium | **Project RAG/dataset relationship is undefined.** Project synthesis deliberately uses workspace evidence and skips RAG; generic RAG/dataset APIs are separate → keep RAG out of authority, optionally allow approved immutable evidence references after the core. | delivery/job presentation `rag_used=False`; `backend/app/rag`; datasets APIs | 3G optional. Prompt-injection/provenance/budget tests before enabling; not an MVP blocker. |
| ASTRA-G020 | Low | **Broader agents/runtimes could distract from reliability.** No need for cloud/multi-agent/general images → explicitly defer. | product objective and Stage 2C constraints | No implementation. Revisit only after 3H gates. |

## 8. Proposed final target architecture

The target remains a local single-node system with SQLite, one FastAPI process, one durable local worker process, Docker Desktop/WSL2, and the existing React chat interface.

```text
browser chat
  -> durable chat request (backend identity)
  -> canonical project API
  -> ProjectControlPlane.execute()
       transaction: ProjectRun + immutable revision/grant/artifact link
                    + ProjectEvent + attempt/outbox/cancellation-outbox
  -> project worker dispatcher
       -> mutation handler (trusted host writer, exact spec only)
       -> Docker handler (snapshot, pinned image, no network)
  -> ProjectWorkerService terminal ingestion
       -> canonical RECORD_* or RECOVER_ATTEMPT command
  -> read-only event projector + ProjectCoordinatorService.reconcile()
  -> coordinator artifact processor
       -> immutable evidence/preview/diagnosis/handoff artifact
       -> canonical command (never a direct lifecycle write)
  -> canonical ProjectReadModel + delivery projection
  -> one reload-safe chat card
```

Required design rules:

- Store artifacts before a command references them, and store artifact linkage/event atomically where authority changes.
- A projector may update compatibility/read tables but cannot issue decisions based on their status.
- A coordinator processor may read canonical state, create a bounded artifact, and submit one command. It cannot write `project_runs`.
- Worker result reconciliation is idempotent on `worker_request_id` plus terminal status and must be complete before the queue row is marked reconciled.
- Cancellation is a durable request to the worker first. A running attempt becomes `cancelling`; it becomes cancelled only after worker/container acknowledgement, except pre-dispatch cancellation.
- Mutation cancellation is allowed before commit. After commit begins, the read model reports `finishing_mutation`; recovery deterministically commits or restores the full set.
- Handoff is a canonical artifact. `REQUEST_HANDOFF` validates and queues/prepares it; `FINALIZE_PROJECT` accepts the exact artifact hash once, rather than requiring two opaque finalize calls.
- Compatibility readers are explicitly selected by record generation; absence of canonical data in a new project is corruption, not permission to infer state.

Capability classification:

| Class | Capabilities |
|---|---|
| Required reliable core | G001–G013, G016; Python/Node; one worker; one repair; canonical card/reload; exact approvals; migration; Docker fail-closed |
| Useful after reliable core | richer multi-file evidence, operator diagnostics/retention, dependency preflight, provider-neutral model invocation |
| Optional future expansion | approved project RAG evidence, more local providers, more reviewed runtime profiles, performance/RSS work |
| Rejected/deferred | cloud/distributed queues, Kubernetes/Celery/Redis, multi-agent delegation, arbitrary images, request-time installation, networked execution, automatic approval, host execution, real-repository container writes |

## 9. Remaining stage roadmap

| Stage | Objective | Why now / exit checkpoint |
|---|---|---|
| 3A | Add restart-safe schema migrations, artifact/model/cancellation contracts and ownership guards | Every later stage changes durable state. Exit: upgrade/restart tests pass and no behavior regresses. |
| 3B | Create new projects and immutable artifacts canonically; add typed side-effect-free read API | Removes legacy-first creation before automation. Exit: new project/read/reload never needs reconciliation-on-GET. |
| 3C | Make worker terminal ingestion, cancellation, projections and next-intent creation converge | Closes duplicate/stranded execution and running-cancel risks. Exit: all crash points converge to one attempt/result/intent. |
| 3D | Process work-unit, deterministic verification and handoff coordinator intents | Enables the automatic approval-controlled happy path after authority is coherent. Exit: multi-work-unit happy path reaches handoff with exact approvals. |
| 3E | Add one bounded diagnosis/repair cycle through canonical artifacts/worker | Failure path follows the same authority and isolation guarantees. Exit: one repair, fresh verification, explicit stop after failure. |
| 3F | Move chat/API/frontend to canonical read model and isolate historical fallback | UI can no longer revive dual authority. Exit: one card survives all reload/stale/unavailable states without inference. |
| 3G | Expand bounded synthesis/model evidence and optional approved RAG boundary | Improve real-task success only after lifecycle completion is reliable. Exit: benchmark uplift without integrity regression. |
| 3H | Remove mutation/execution compatibility, add operator startup/diagnostics, run full MVP gates | Final release gate. Exit: no host project execution paths, clean upgrade, documented startup, benchmark and browser acceptance. |

## 10. Exact file-level implementation plan

All paths or symbols marked **NEW** are proposals. Unmarked paths and symbols exist at this baseline.

### Stage 3A — versioned migration and contract foundation

**Objective and ordering.** Establish a safe schema evolution mechanism and the durable identities needed by artifacts, model calls, projection, and cancellation. It is first because later changes cannot safely ship through more service-local `CREATE TABLE`/`ALTER TABLE` calls.

**Prerequisites and invariants.** Start from the clean Stage 2C checkpoint. Preserve every Stage 0–2C invariant. Migrations may normalize or tag legacy data but may not invoke models, workers, containers, mutations, or lifecycle transitions.

| Change type | Exact plan |
|---|---|
| Create | **NEW** `backend/app/database/migrations.py`: `SchemaMigration` (version, name, checksum, apply callback), `MigrationError`, `apply_schema_migrations(database_path)`, `current_schema_version()`, `assert_schema_compatible()`. Inputs: SQLite path and ordered registry. Outputs: applied-version report. Errors: checksum mismatch, newer schema, interrupted/corrupt migration. Interacts with every existing `initialize()`. |
| Create | **NEW** `backend/app/project_artifacts/__init__.py`, **NEW** `backend/app/project_artifacts/contracts.py`, **NEW** `backend/app/project_artifacts/store.py`. `ProjectArtifactType`, `ProjectArtifact`, `ProjectArtifactBinding`, `ProjectArtifactStore.initialize()/put()/get()/list_for_project()/verify()`. Inputs include project, plan/scope/manifest, attempt/intent, bounded payload/reference and content hash. Output is immutable artifact identity. Reject unknown schema, oversized payload, binding/hash mismatch, duplicate ID with different content. |
| Create | **NEW** `backend/app/project_control/cancellation.py`: `ExecutionCancellation`, `ExecutionCancellationStatus`, `build_execution_cancellation()`. It represents cancellation delivery, not the lifecycle decision. |
| Create | **NEW** `backend/app/project_models/contracts.py`, **NEW** `backend/app/project_models/store.py`: `ProjectModelInvocation`, `ProjectModelInvocationStatus`, `ProjectModelInvocationStore`. Key by coordinator intent + evidence hash + purpose + provider/model profile; store lease, request hash, bounded terminal response/error and usage. |
| Modify | `backend/app/database/repository.py:AnalysisRepository.initialize()` to call the migration runner once and stop issuing schema evolution independently. Keep repository-specific row conversion methods. |
| Modify | `ProjectControlPlane.initialize()`, `ProjectWorkerQueue.initialize()`, `FileMutationEngine.initialize()`, `ProjectCoordinatorService.initialize()` so they validate required migration version and create nothing after the compatibility window. During 3A they may retain idempotent create calls behind one temporary migration feature flag. |
| Modify | `backend/app/project_control/contracts.py` add **NEW** versioned artifact-reference fields and **NEW** `CANCELLING` attempt/read status additively; add command payload validation without changing behavior yet. |
| Database | Add `schema_migrations(version PRIMARY KEY, name, checksum, applied_at)`, `project_artifacts`, `project_model_invocations`, `project_execution_cancellations`, and `project_projection_checkpoints`. Baseline migration records the current Stage 2C schema; later migrations add tables. Use `BEGIN IMMEDIATE`, foreign keys, indexes by project/status/created time, and checksum verification. |
| API/frontend | No product API behavior. Runtime capability adds read-only `database_schema_version` and `database_migration_status`. Frontend ignores these additive fields. |
| Tests | **NEW** `tests/test_database_migrations.py`, `tests/test_project_artifacts.py`, `tests/test_project_model_invocations.py`. Add copies/fixtures representing Stage 0, Stage 1, Stage 2A/B/C layouts; interrupt before/after each DDL/data step; rerun exactly; reject checksum/newer versions. Extend `tests/test_project_control.py` for additive contract compatibility. |

Verification commands:

```bash
TMP=/tmp TEMP=/tmp .venv/bin/python -m pytest -q tests/test_database_migrations.py tests/test_project_artifacts.py tests/test_project_model_invocations.py tests/test_project_control.py
TMP=/tmp TEMP=/tmp .venv/bin/python -m compileall -q backend/app/database backend/app/project_artifacts backend/app/project_models backend/app/project_control
git diff --check
```

Manual reproduction: copy a Stage 2C database, start FastAPI once, stop it during a test-injected migration boundary, restart, inspect `schema_migrations`, and confirm existing conversation/project records still read while no worker request/model invocation was created.

Acceptance: migrations are transactional/idempotent; a newer or checksum-mismatched database fails startup with a controlled diagnostic; all existing records remain readable; no lifecycle event is emitted; only schema changes occur.

Non-goals: canonical project creation, artifact processors, worker behavior, UI changes, migration of every legacy payload.

Checkpoint commit message: `Add versioned Astra project schema foundation`

Rollback: stop binaries, restore the pre-migration SQLite backup, and revert code. Do not attempt destructive down-migrations. Additive tables may remain unused if code is rolled back.

### Stage 3B — canonical project creation, artifacts, and read API

**Objective and ordering.** Make `ProjectRun` and immutable `ProjectArtifact` the first records for new work. Provide a typed, side-effect-free read model before terminal/projector automation.

**Prerequisites and invariants.** Stage 3A migrations are deployed. Historical deliveries remain readable. GET requests do not migrate or write.

| Change type | Exact plan |
|---|---|
| Create | **NEW** `backend/app/project_control/project_service.py`: `CanonicalProjectService.create_project()`, `record_specification()`, `record_manifest()`, `record_plan()`, `revise_scope()`. Inputs: backend IDs, completed folder authority, deterministic specification/plan/artifact, idempotency key. Outputs: `ProjectReadModel`. All lifecycle calls go through `ProjectControlPlane.execute()`. |
| Create | **NEW** `backend/app/project_api/contracts.py`: `CanonicalProjectCreateRequest`, `CanonicalProjectResponse`, `CanonicalProjectCollection`, `CanonicalProjectActionRequest`, `CanonicalArtifactSummary`. Strict schemas and versions; response embeds only canonical current state plus bounded artifact summaries. |
| Create | **NEW** `backend/app/project_api/routes.py`: `create_project_router(...)`; routes **NEW** `POST /chat/projects`, `GET /chat/projects/{project_run_id}`, `GET /chat/conversations/{conversation_id}/projects`, `GET /chat/projects/{project_run_id}/artifacts`. Existing delivery URLs delegate for compatibility. |
| Modify | `backend/app/project_control/contracts.py`: add **NEW** `current_artifact_ids`, exact current preview/command/verifier/repair/handoff artifact IDs and hashes to `ProjectRun`/`ProjectReadModel`; add explicit **NEW** `RECORD_*_ARTIFACT` command types/fields or replace unbound preview payloads with artifact-bound variants. |
| Modify | `ProjectControlPlane._apply()`: require artifact ID/hash/bindings for specification, plan, patch preview, command preview, verifier result and handoff; load/verify artifacts through an injected store. Do not let artifact contents select a transition. |
| Modify | `backend/app/main.py:create_app()` to construct `ProjectArtifactStore`, `CanonicalProjectService`, include the new router, and make `_start_project_delivery()` call canonical creation first. Compatibility delivery is produced by projection, not used as input. |
| Modify | `backend/app/project_delivery/service.py:create_delivery_job()`, `build_execution_plan()`, `generate_handoff()` to become pure deterministic builders callable by canonical service; no database writes or lifecycle decisions. |
| Modify | `AnalysisRepository.get_chat_conversation()`/conversation route to add canonical `projects` additively in hydration v2 while retaining v1 fields for one compatibility release. |
| Database | Populate `project_artifacts`. Add immutable unique keys `(project_run_id, artifact_type, binding_hash, revision_number)` where applicable. Add `canonical_generation` tag to legacy reconciliation/projection records. |
| Frontend | Add response types to `frontend/src/types/contracts.ts` and typed client methods in `frontend/src/clients/astraClient.ts`; do not switch the card yet. |
| Tests | **NEW** `tests/test_canonical_project_service.py`, `tests/test_project_api.py`. Modify `tests/test_project_delivery.py` for create-before-read, side-effect-free GET, exact artifact binding, duplicate create, refresh during creation, and historical delivery reads. Add contract fixtures to `frontend/tests/projectDeliveryStage9.test.ts`. |

Verification commands:

```bash
TMP=/tmp TEMP=/tmp .venv/bin/python -m pytest -q tests/test_project_artifacts.py tests/test_canonical_project_service.py tests/test_project_api.py tests/test_project_delivery.py
cd frontend && ./node_modules/.bin/tsc -b --pretty false
git diff --check
```

Manual reproduction: connect a folder, submit a new project request, stop before any preparation, reload the conversation and direct project URL, and verify one `project_run_id`, canonical spec/manifest/plan artifacts, no new rows on repeated GET, and no hidden project job.

Acceptance: new creation is canonical-first; all referenced artifacts verify; GET is side-effect-free; historical delivery remains read-only/reapproval-safe; frontend types compile.

Non-goals: worker terminal follow-on, coordinator processing, repair, replacing the visible card.

Checkpoint commit message: `Make project creation and artifacts canonical`

Rollback: route new creation back to the compatibility adapter while retaining additive artifacts; do not delete them. Restore pre-stage DB only if migration itself is faulty.

### Stage 3C — terminal reconciliation, cancellation, and compatibility projection

**Objective and ordering.** Make queue completion and cancellation converge exactly once into canonical state, a read projection, and the next coordinator intent. This precedes autonomous work because stranded or duplicated terminal edges would otherwise multiply work.

**Prerequisites and invariants.** Stages 3A–3B. Queue rows remain evidence only. No projector or worker writes `project_runs` directly. No worker task is re-executed during recovery.

| Change type | Exact plan |
|---|---|
| Create | **NEW** `backend/app/project_projection/service.py`: `ProjectProjectionService.project_event()`, `rebuild_project()`, `rebuild_all()`. Input is ordered `ProjectEvent` plus canonical artifact/read data. Output updates compatibility `project_delivery_jobs`, chat action, and projection checkpoint. Errors pause projection, never canonical state. |
| Create | **NEW** `backend/app/project_workers/reconciliation.py`: `TerminalResultReconciler.reconcile(worker_request_id)`. Move result mapping from `ProjectWorkerService._reconcile_result()` into a transaction-aware service that stores/validates result artifact, submits one canonical command, records reconciliation, then invokes projector/coordinator idempotently. |
| Create | **NEW** `backend/app/project_workers/cancellation.py`: `CancellationDispatcher.dispatch_pending()`, `acknowledge()`, `recover()`. It maps exact canonical cancellation rows to `ProjectWorkerService.request_cancel()` and waits for queue/container acknowledgement. |
| Modify | `ProjectWorkerService._reconcile_terminal()`, `_recover_canonical_attempt()`, `dispatch_pending()` to delegate and report granular recovered/deferred IDs. Domain nonzero remains a canonical failed result; infrastructure failure remains interrupted/blocked. |
| Modify | `ProjectControlPlane._apply(CANCEL_PROJECT)`: pending pre-dispatch work may cancel immediately; enqueued/leased work becomes `cancelling` and creates `ExecutionCancellation` in the same transaction. Final cancellation is a new artifact-bound/worker-acknowledged command **NEW** `ACKNOWLEDGE_EXECUTION_CANCELLATION`. |
| Modify | `backend/app/project_workers/__main__.py:worker_cycle()` to recover cancellation/outbox/result/projection gaps in deterministic order before claiming new work. Keep bounded one-cycle behavior and idle-write suppression. |
| Modify | `backend/app/main.py:chat_project_delivery_cancel()` to submit only the canonical cancel command and return canonical `cancelling`/terminal state. Remove its direct assumption that attempt cancellation is complete. |
| Modify | `_validated_project_delivery()` and `_reconcile_completed_delivery_mutation()` so GET only reads projection. Move projection logic into `ProjectProjectionService`; remove read-side coordinator creation. |
| Database | Use `project_execution_cancellations` and `project_projection_checkpoints`. Store canonical terminal result artifact and reconciliation key. Do not add a second lifecycle status. |
| API/frontend | Canonical read exposes `cancelling`, cancellation ID/status, projection lag/recovery classification. Modify `frontend/src/state/projectDeliveryState.ts` and `frontend/src/App.tsx:ProjectDeliveryCard` to display it additively without choosing a transition. |
| Tests | **NEW** `tests/test_project_terminal_reconciliation.py`, `tests/test_project_projection.py`, `tests/test_project_cancellation.py`. Extend dispatch/worker/mutation/Docker tests with crash after queue terminal, artifact store, canonical command, projection, intent creation; queued/running/mutation-commit cancellation; concurrent duplicate reconciler. |

Verification commands:

```bash
TMP=/tmp TEMP=/tmp .venv/bin/python -m pytest -q tests/test_project_terminal_reconciliation.py tests/test_project_projection.py tests/test_project_cancellation.py tests/test_project_worker_dispatch.py tests/test_project_workers.py tests/test_project_mutation_worker.py
TMP=/tmp TEMP=/tmp .venv/bin/python -m pytest -q -m docker_integration tests/test_project_worker_docker_integration.py -k 'cancellation or reconciliation or restart'
git diff --check
```

Manual reproduction: queue a long Docker command, cancel from chat, reload while cancelling, restart backend and worker independently, and verify the same worker/container ID is stopped, one attempt becomes cancelled/interrupted as specified, one terminal artifact exists, and the card/project survives.

Acceptance: one project/attempt/worker request/result/projection; no process survives cancellation; no cancelled task later reports success; failures in projection or intent creation recover without execution; GET writes nothing.

Non-goals: work-unit/repair artifact generation, frontend card replacement, new synthesis.

Checkpoint commit message: `Reconcile project results and cancellation canonically`

Rollback: disable the new projector/cancellation dispatcher feature flag, stop workers, and use recovery tooling to settle in-flight cancellation rows before reverting. Never restore host fallback.

### Stage 3D — coordinator artifact processor and work-unit loop

**Objective and ordering.** Process durable coordinator intents for work-unit preparation, non-executable verification, and handoff, automatically preparing the next exact preview while retaining user approval for mutations and commands.

**Prerequisites and invariants.** Stages 3A–3C. A processor can create an artifact and submit a canonical command only. It cannot approve, mutate files, run repository code on host, or infer lifecycle from delivery status.

| Change type | Exact plan |
|---|---|
| Create | **NEW** `backend/app/project_coordinator/execution.py`: `ProjectCoordinatorExecutor.run_once(worker_id)`, `CoordinatorIntentHandler` protocol, `PrepareWorkUnitHandler`, `DeterministicVerificationHandler`, `PrepareHandoffHandler`. Input is claimed intent/current canonical read. Output is immutable artifact + exact canonical command + completed intent. Errors: stale binding (cancel intent/reconcile new), policy/budget block, infrastructure retry without second model call. |
| Create | **NEW** `backend/app/project_coordinator/evidence.py`: `build_work_unit_evidence()`, `build_handoff_evidence()`, byte accounting and artifact bindings using approved scope/plan/manifest. |
| Modify | `ProjectCoordinatorService` add **NEW** `heartbeat()`, **NEW** `cancel_stale_for_project()`, exact completion idempotency, and explicit retry classification. Expired claims return pending only when no durable model/artifact result exists. |
| Modify | `backend/app/project_workers/__main__.py:build_runtime()/worker_cycle()` to construct and run `ProjectCoordinatorExecutor` in the same separate worker process after reconciliation and before idle wait. Worker heartbeat reports coordinator capabilities separately from project execution types. |
| Modify | `ProjectControlPlane._apply(BEGIN_WORK_UNIT/RECORD_PATCH_PREVIEW/REQUEST_VERIFICATION/REQUEST_HANDOFF/FINALIZE_PROJECT)`: bind intent/artifact IDs; complete preparation attempts correctly; create the next pending action. Replace the current two-call finalize sequence with exact handoff artifact finalization while retaining historical replay support. |
| Modify | `backend/app/project_jobs/workflow.py:prepare_job_patch_bundle()` and `backend/app/project_delivery/verifier.py:run_deterministic_verifier()` into pure handlers fed by canonical artifacts. Do not store/update legacy job/delivery status. |
| Modify | `ProjectProjectionService` to render preview, deterministic verifier evidence, work-unit completion, next unit, and handoff from canonical artifacts. |
| Database | Reuse coordinator/artifact/model tables; add intent heartbeat/attempt fields through a migration. Budgets live in canonical plan/run and model invocation rows, not browser state. |
| API/frontend | Existing approve patch/command buttons consume canonical artifact identity/hash. Modify `frontend/src/clients/astraClient.ts`, `frontend/src/state/projectDeliveryState.ts`, and `frontend/src/App.tsx:ProjectDeliveryCard` to show coordinator pending/claimed/completed/failure and the backend-provided next exact action. |
| Tests | **NEW** `tests/test_project_coordinator_execution.py`, `tests/test_project_work_unit_loop.py`, `tests/test_project_handoff_artifact.py`. Extend coordinator/control/delivery/model tests. Reload/crash at intent claim, evidence built, model returned, artifact stored, command applied, intent completion. Assert one model invocation. |

Verification commands:

```bash
TMP=/tmp TEMP=/tmp .venv/bin/python -m pytest -q tests/test_project_coordinator.py tests/test_project_coordinator_execution.py tests/test_project_work_unit_loop.py tests/test_project_handoff_artifact.py tests/test_project_control.py
cd frontend && ./node_modules/.bin/tsc -b --pretty false
git diff --check
```

Manual reproduction: approve a two-work-unit plan; observe automatic first patch preview; approve/apply it; allow deterministic checks; approve any exact subprocess verifier; observe the second preview; complete and prepare/finalize the exact handoff. Reload/restart worker at each pending/claimed boundary.

Acceptance: next preview is automatic but never applied automatically; deterministic non-executable checks may run without approval; every subprocess command is separately approved; all work units/criteria/handoff are artifact-bound; one intent and model invocation per trigger.

Non-goals: repair, multiple autonomous repair cycles, RAG, new languages.

Checkpoint commit message: `Process canonical project coordinator artifacts`

Rollback: stop the worker, leave pending intents durable, disable processors, and keep manual canonical actions available. Artifacts remain immutable history.

### Stage 3E — one-repair canonical orchestration

**Objective and ordering.** Put failure evidence, diagnosis, one repair preview, approval, mutation, and fresh verification on the same canonical pipeline.

**Prerequisites and invariants.** Stage 3D happy path is complete. Exactly one automatic repair preparation per failed work unit/project budget. A failed repair verification stops; only an explicit user command can create another scope/plan/repair revision.

| Change type | Exact plan |
|---|---|
| Create | **NEW** `backend/app/project_repair/contracts.py`: `RepairCycle`, `RepairCycleStatus`, `FailureEvidenceArtifact`, `DiagnosisArtifact`, `RepairPreviewArtifact`; strict bindings and limits. |
| Create | **NEW** `backend/app/project_repair/service.py`: `CanonicalRepairService.capture_failure()`, `begin_diagnosis()`, `record_diagnosis()`, `record_preview()`, `finish_cycle()`. It creates artifacts and canonical commands only. |
| Modify | `ProjectCoordinatorExecutor` add `PrepareRepairHandler`, invoking `diagnose_project_failure()` and bounded synthesis through `ProjectModelInvocationStore`. |
| Modify | `ProjectWorkerService` domain-failure reconciliation to store redacted/bounded failure artifact before `RECORD_COMMAND_RESULT`/`RECORD_VERIFIER_RESULT`; infrastructure failures do not create repair previews. |
| Modify | `backend/app/project_analysis/diagnosis/evidence.py` and `backend/app/project_analysis/diagnosis/service.py` accept canonical bindings/artifacts; keep deterministic-first confidence and strict model validation. |
| Modify | `ProjectControlPlane` add **NEW** repair-cycle ID/count/current artifact fields; `INITIATE_REPAIR` requires failure artifact; repair `RECORD_PATCH_PREVIEW` requires diagnosis and repair artifact; scope expansion executes `REVISE_SCOPE` and invalidates approval instead. |
| Modify | `ProjectProjectionService` and delivery presentation to project repair evidence/status without `project_jobs.repair` authority. |
| Database | Store repair cycle/artifacts canonically; either normalized `project_repair_cycles_v2` or artifact plus indexed cycle table. Do not overwrite legacy `project_repair_cycles`. |
| API/frontend | Add exact `approve repair`, `revise scope`, and `stop` actions using canonical artifact/revision/version. Modify the corresponding delivery/patch routes in `backend/app/main.py`, client methods in `frontend/src/clients/astraClient.ts`, repair parsing in `frontend/src/state/projectDeliveryState.ts`, and repair presentation in `frontend/src/App.tsx`. Existing patch approval endpoint may delegate. |
| Tests | **NEW** `tests/test_project_repair_coordinator.py`, `tests/test_project_repair_artifacts.py`. Extend `tests/test_project_diagnosis.py`, worker execution, mutation, control, frontend repair tests. Include unsupported output, stale failure, scope expansion, model unavailable, duplicate diagnosis, one-cycle limit, repair fails verification, rollback after repair. |

Verification commands:

```bash
TMP=/tmp TEMP=/tmp .venv/bin/python -m pytest -q tests/test_project_repair_artifacts.py tests/test_project_repair_coordinator.py tests/test_project_diagnosis.py tests/test_project_worker_execution.py tests/test_project_mutation_worker.py
cd frontend && node --test --experimental-strip-types tests/projectRepairStage8.test.ts tests/projectDeliveryStage9.test.ts
git diff --check
```

Manual reproduction: approve a command that fails, inspect redacted failure/diagnosis/repair preview, reload, approve the exact repair, apply through worker, approve/freshly execute verification, then repeat with a repair that fails and confirm no second automatic preview appears.

Acceptance: domain failure yields one bounded diagnosis and at most one repair preview; infrastructure failure blocks without diagnosis; repair approval is exact and separate; re-verification is fresh; further repair requires explicit user action; rollback stays available.

Non-goals: self-approval, unlimited retries, speculative agents, automatic scope expansion.

Checkpoint commit message: `Add one canonical project repair cycle`

Rollback: disable repair intent processing; retain artifacts and project in `repair_required`/blocked with explicit rollback or scope-revision actions.

### Stage 3F — canonical chat/API/frontend reload

**Objective and ordering.** Make the browser consume one canonical project response/card and confine legacy fallbacks to tagged historical records.

**Prerequisites and invariants.** Stages 3B–3E supply complete canonical states. Browser state is never authority and temporary lookup failure never clears a valid project/conversation.

| Change type | Exact plan |
|---|---|
| Create | **NEW** `frontend/src/state/projectControlState.ts`: `CanonicalProjectAction`, `canonicalProjectActionFromResponse()`, `mergeCanonicalProjectAction()`, `exactProjectMutationRequest()`. It validates schema/type and displays backend `next_permitted_actions`; no lifecycle mapping/inference. |
| Create | **NEW** `frontend/src/components/ProjectControlCard.tsx`: extracted one-card presentation for plan, approval, coordinator, queue, execution, verification, repair, rollback and handoff. Buttons are rendered from backend actions with exact artifact/version bindings. |
| Modify | `backend/app/schemas/api.py:ChatConversationDetail` to hydration v2 containing **NEW** `projects: list[CanonicalProjectResponse]`; continue returning v1 legacy fields only for tagged historical records. |
| Modify | `frontend/src/clients/astraClient.ts` replace project `Record<string, unknown>` with canonical request/response types and add canonical create/read/action/cancel methods. Keep old methods in a `LegacyProjectClient` compatibility section. |
| Modify | `frontend/src/App.tsx:restoreConversationMessages()`, `refreshProjectDelivery()`, delivery action handlers and `ProjectDeliveryCard` usage. Restore projects solely from `detail.projects`; merge by `project_run_id`; poll canonical read while active/cancelling; never auto-submit an action/model/worker request. |
| Modify | `frontend/src/state/conversationReloadState.ts:shouldClearActiveConversation()` retain the existing 404-only rule; add project lookup resolution where only definitive `project_not_found` for a backend-known conversation removes the card. 409/429/5xx/network errors retain it. |
| Modify | `frontend/src/state/projectDeliveryState.ts` move `legacyLifecycle()` and legacy parser to **NEW** `frontend/src/state/legacyProjectDeliveryState.ts`, reachable only for `record_generation="legacy"`. |
| Database | No new lifecycle store. Add the hydration/projection generation tag through the Stage 3A migration runner and backfill only deterministic canonical/legacy classification. Do not rewrite historical action payloads on read. |
| API | Return versioned typed stale state, isolation unavailable, policy rejection, timeout, cancellation, recovery and projection-lag errors with latest canonical project. Add read-only events/artifacts links, not raw sensitive evidence. |
| Tests | **NEW** `frontend/tests/projectControlState.test.ts`, `frontend/tests/projectControlCard.test.ts`; extend conversation reload, chat single-page, Stage 9/8 tests. Backend `tests/test_project_api.py` covers hydration v2, temporary failure, permanent missing, stale action. Add browser manual test script/checklist in docs later. |

Verification commands:

```bash
TMP=/tmp TEMP=/tmp .venv/bin/python -m pytest -q tests/test_project_api.py tests/test_project_delivery.py
cd frontend && node --test --experimental-strip-types tests/conversationReloadState.test.ts tests/projectControlState.test.ts tests/projectControlCard.test.ts tests/projectDeliveryStage9.test.ts tests/projectRepairStage8.test.ts tests/chatSinglePage.test.ts
cd frontend && npm run lint && npm run build
git diff --check
```

Manual reproduction: successful flow, failed verification/repair, queued/running cancellation, reload before dispatch/during execution/after queue terminal/before reconciliation, stale button, worker unavailable, temporary API failure, and permanently nonexistent project. Each uses one card and the same IDs.

Acceptance: new records never use `legacyLifecycle()`; browser never chooses a transition; one canonical card rehydrates once; transient errors preserve identity; definitive absence clears safely; no duplicate request/model/attempt/worker run.

Non-goals: visual redesign, dashboard, WebSocket requirement, cloud collaboration.

Checkpoint commit message: `Move Astra project chat to canonical state`

Rollback: feature-flag hydration/card to the prior compatibility renderer while canonical APIs continue. Do not restore browser authority or mutation fallback.

### Stage 3G — bounded synthesis, model, agent, and RAG plan

**Objective and ordering.** Improve bounded multi-file task success and make every model invocation durable/idempotent without expanding authority. RAG stays optional and evidence-only.

**Prerequisites and invariants.** Canonical loop and card pass Stages 3A–3F. Models never approve, execute, mark verification passed, or select unapproved paths.

| Change type | Exact plan |
|---|---|
| Create | **NEW** `backend/app/project_analysis/model_synthesis/orchestrator.py`: `CanonicalSynthesisOrchestrator.prepare_patch()`/`prepare_repair()`. Inputs: coordinator intent, canonical evidence artifact, provider profile, invocation store. Outputs: strictly validated preview artifact or typed clarification/block. |
| Create | **NEW** `backend/app/project_analysis/model_synthesis/toolchain.py`: `ProjectToolchainProfile`, `detect_toolchain_requirements()`, `check_runtime_compatibility()`. No install/build/pull; produces typed support evidence. |
| Modify | `backend/app/project_analysis/model_synthesis/evidence.py:build_evidence_package()` to include approved requirements, criterion hashes, plan/work-unit/scope IDs, manifest/config excerpts, dependency relationships, relevant tests, complete byte accounting, and explicit missing evidence. Keep maximums bounded and configurable in canonical plan. |
| Modify | `backend/app/project_analysis/model_synthesis/contracts.py` to support coherent bounded multi-file create/modify/delete and exact replacements while rejecting absolute/traversal/unapproved paths, unknown fields, malformed operations, missing hashes, secrets, approval phrases, and unexplained files. |
| Modify | `backend/app/project_analysis/model_synthesis/gateway.py` keep `SynthesisGateway` provider-neutral and Ollama implementation; route through durable invocation store. Provider unavailable is a typed block; there is no silent untracked fallback. |
| Modify | `backend/app/project_analysis/validation.py:prevalidate_virtual_files()` to bind full virtual post-change tree, syntax/parser result, imports/references, test/config relationship, scope, manifest and artifact hash. |
| Optional | **NEW** `backend/app/project_analysis/project_rag.py`: `ApprovedProjectEvidenceRetriever`. It may reference only user-approved, provenance-bearing dataset/corpus artifacts and cannot expand scope. Disabled by default; generic `backend/app/rag` indexes never become project authority. |
| Explicitly no agent layer | Do not add multi-agent orchestration. The durable coordinator plus typed handlers is the agentic loop needed for local MVP. |
| Database | Add no new tables beyond Stage 3A. Extend `project_model_invocations` and `project_artifacts` only through versioned additive migrations if toolchain/evidence summary columns are needed; payload hashes remain immutable. |
| API/frontend | Extend **NEW in Stage 3B** `backend/app/project_api/contracts.py:CanonicalProjectResponse`, `frontend/src/types/contracts.ts`, and **NEW in Stage 3F** `frontend/src/components/ProjectControlCard.tsx` with typed provider/toolchain/evidence/clarification summaries. Do not expose raw prompts/responses or let the browser select evidence. |
| Tests | **NEW** `tests/test_project_synthesis_orchestrator.py`, `tests/test_project_toolchain_profile.py`; extend model synthesis/analysis/diagnosis. Optional RAG tests require provenance, prompt-injection resistance, byte budget, stale binding. Add real-repository benchmark assertions. |

Verification commands:

```bash
TMP=/tmp TEMP=/tmp .venv/bin/python -m pytest -q tests/test_model_synthesis.py tests/test_project_synthesis_orchestrator.py tests/test_project_toolchain_profile.py tests/test_project_analysis.py tests/test_project_diagnosis.py
git diff --check
```

Manual reproduction: run one Python and one Node multi-file task with local Ollama enabled, reload after provider response, verify one invocation and exact preview; repeat with Ollama down, missing dependency, unsupported language, prompt-injection text, and optional approved dataset evidence.

Acceptance: no duplicate model invocation; validated coherent previews only; typed unsupported dependency/language/provider states; benchmark improves without approval/scope/security regression; RAG, if enabled, is provenance-bound advisory evidence only.

Non-goals: online model dependency, automatic dependency installation, arbitrary tool calls, autonomous agents, more than reviewed Python/Node runtime.

Checkpoint commit message: `Expand bounded canonical project synthesis`

Rollback: disable canonical model mode and optional project RAG; deterministic preparation remains available; immutable invocation/artifacts remain history.

### Stage 3H — compatibility retirement, operations, and MVP gates

**Objective and ordering.** Remove remaining host mutation/execution authority, finish migration/read compatibility, add operational diagnostics/startup documentation, and run the full release gates.

**Prerequisites and invariants.** Stages 3A–3G complete. Historical records have been tested through import/read/recovery. No reliability tradeoff is permitted for compatibility.

| Change type | Exact plan |
|---|---|
| Remove/modify | In `backend/app/project_workers/__main__.py:build_runtime()` delete `ProjectSubprocessExecutor` selection and `ASTRA_ALLOW_LEGACY_PROJECT_EXECUTION`; in `backend/app/main.py` remove project-root calls to `execute_assignment_command()`, `apply_project_patch()`, and `rollback_project_patch()` from project chat compatibility routes. Keep those helpers only if non-project assignment features still require them and prove scope separation. |
| Modify | `ProjectDeliveryControlAdapter` to import/read historical records only. `apply_transition()` cannot be called for `canonical_generation`. Remove read-side reconciliation. |
| Modify | Legacy routes in `backend/app/main.py` for `/chat/projects/deliveries`, jobs, patches, commands and rollback: canonical IDs delegate to canonical API; historical mutation attempts return typed `historical_record_read_only` or require explicit one-time migration/reapproval. |
| Create | **NEW** `backend/app/operations/project_doctor.py`: `collect_project_runtime_diagnostics()` reports DB schema, pending migrations, worker heartbeat, Docker/image digest, orphan/reconciliation/cancellation/projection counts and bounded last failures. It never pulls/builds/repairs. |
| Create | **NEW** `scripts/astra_project_doctor.py` and **NEW** `scripts/run_local_astra.sh`. The run script validates prebuilt image/env, starts backend/worker/frontend with explicit PIDs/log paths, and stops cleanly; it never installs/builds/pulls. |
| Create | **NEW** `tests/test_project_mvp_e2e.py`, `tests/test_project_compatibility_retirement.py`, `tests/test_project_operator_status.py`; add/lock deterministic benchmark baseline under **NEW** `benchmarks/project_mvp/baseline.json` if the current benchmark area has no equivalent project baseline. |
| Database | Final migration tags imported historical records, records compatibility-removal version, and verifies no unsupported active attempt is silently runnable. No destructive table drop occurs in the first retirement release; old tables remain read-only for rollback/history. |
| API/frontend | Remove new-project legacy fields after the compatibility release. Modify `frontend/src/clients/astraClient.ts`, **NEW in Stage 3F** `frontend/src/state/legacyProjectDeliveryState.ts`, and `frontend/src/App.tsx` so historical records are visibly read-only and canonical records use only `ProjectControlCard`. |
| Modify | README, `docs/stage2c-container-isolation-and-control.md`, `docs/stabilization-checkpoint.md`; **NEW** `docs/astra-local-operations.md`; mark `docs/FINAL_SYSTEM_STATUS.md` historical and fix `docs/MIGRATION.md` links. |
| Gates | Full backend, all 19+ Docker tests, all frontend tests, TypeScript, ESLint, production build, `git diff --check`, browser journeys, benchmark gates, zero managed containers/process leaks. |

Verification commands:

```bash
TMP=/tmp TEMP=/tmp .venv/bin/python -m pytest -q
TMP=/tmp TEMP=/tmp .venv/bin/python -m pytest -q -m docker_integration tests/test_project_worker_docker_integration.py
cd frontend && node --test --experimental-strip-types tests/*.test.ts
cd frontend && npm run lint && npm run build
git diff --check
git status --short
```

Manual reproduction: clean WSL2 startup from the operations doc; successful Python and Node projects through handoff; failed verification/repair; cancellation; every reload point; stale action; worker/Docker unavailable; historical record read/migration; shutdown and orphan/process inspection.

Acceptance: no reachable host project-code execution or direct host mutation; migration and historical read are safe; operator diagnostics are actionable and redacted; all full-suite/browser/benchmark gates pass; no container/process remains.

Non-goals: cloud/team/distributed operation, automatic image/dependency management, extra toolchains, multi-agent work.

Checkpoint commit message: `Complete Astra reliable local MVP`

Rollback: stop all processes; retain the last compatibility release binary and database backup for read-only access; do not re-enable host mutation/execution. Roll back a release only after all in-flight attempts/cancellations/mutations reconcile.

## 11. Database and migration plan

Migration ownership moves to **NEW** `backend/app/database/migrations.py`. Service `initialize()` methods validate their minimum version but do not independently evolve schema after Stage 3A.

Proposed ordered migrations:

| Version | Change | Data treatment and recovery |
|---|---|---|
| 1 | Record current Stage 2C schema baseline | Introspect required tables/columns; refuse ambiguous partial schema; no payload rewrite. |
| 2 | Add `project_artifacts` and indexes | No backfill. Historical artifacts are imported lazily only through explicit migration command, never GET. |
| 3 | Add `project_model_invocations` | Existing synthesis attempts remain historical; optional importer records hashes/references without re-calling a model. |
| 4 | Add `project_execution_cancellations` and `project_projection_checkpoints` | Existing active attempts are classified for operator review; never auto-cancel/re-execute. |
| 5 | Add canonical artifact references/repair fields | Additive JSON/schema version transition with deterministic upgrader; old v1 records remain read-only if not safely upgradeable. |
| 6 | Tag canonical vs legacy generations and compatibility migration status | New records are canonical. Existing deliveries remain legacy until explicit import/reapproval. |

Rules:

- Back up the SQLite file before the first migration that rewrites data.
- Use one connection, foreign keys on, `BEGIN IMMEDIATE`, checksum each migration, and commit the version row with the change.
- Migration recovery reruns the same idempotent step; never guess past a checksum mismatch.
- Unknown newer schema fails startup. Unsupported record schema returns a typed read error and never silently drops/replays it.
- Existing active Stage 2B/C attempts are recovered or blocked for review using their durable identity. They are never silently rerun.
- Provide `--status`, `--apply`, and `--verify-only` operation modes; startup may apply reviewed additive migrations, but production-readiness documentation must state backup behavior.
- Test upgrade from real fixture databases, concurrent startup, disk-full/interruption simulation, and restore.

## 12. Backend API and contract plan

The canonical API is additive in 3B and becomes the only new-project mutation API in 3F/3H.

| Contract/route | Plan |
|---|---|
| **NEW** `CanonicalProjectResponse` | Version, project ID, lifecycle, state version, revisions/manifest, current immutable artifacts, approvals, active execution/cancellation/coordinator, progress/criteria, failure, evidence summaries, and `next_permitted_actions`. No raw secret-bearing output. |
| **NEW** `POST /chat/projects` | Backend identity and idempotency. Validates conversation/folder binding, stores canonical project/spec/manifest/plan before returning. |
| **NEW** `GET /chat/projects/{id}` | Side-effect-free canonical read. 404 means permanent absence; 409 stale/corrupt and 5xx temporary errors include safe typed classification. |
| **NEW** `GET /chat/conversations/{id}/projects` | Canonical collection for reload. Legacy items are separately tagged summaries, not merged authority. |
| **NEW** `POST /chat/projects/{id}/actions` | Strict discriminated action request: approve exact plan/patch/command/rollback/manual/handoff, cancel, revise scope, retry preparation. Requires project/state/revision/artifact/idempotency bindings. It maps to one `ProjectCommand`. Separate convenience URLs may delegate. |
| Runtime capabilities | Add schema/migration, coordinator capability, queue/reconciliation/cancellation/projection counts, supported profiles. Remains read-only. |
| Hydration v2 | `ChatConversationDetail.projects` contains current canonical responses. Requests/turns remain chat history; legacy jobs/deliveries are tagged history only. |

Contract errors must be versioned and typed: `stale_state`, `missing_approval`, `artifact_mismatch`, `scope_mismatch`, `manifest_mismatch`, `isolation_unavailable`, `unsupported_toolchain`, `unsupported_dependency`, `policy_rejection`, `timeout`, `cancelling`, `cancelled`, `recovery_required`, `projection_delayed`, `historical_record_read_only`. Each error must return the latest safe read model when the project exists.

## 13. Worker and runtime plan

Preserve the separate process and current isolation defaults. The worker cycle order becomes:

1. heartbeat and capability probe;
2. mutation-journal/orphan recovery at process startup;
3. cancellation dispatch/ack recovery;
4. outbox-to-queue dispatch recovery;
5. unreconciled terminal result ingestion;
6. projection/coordinator-intent recovery;
7. claim/execute one project worker request;
8. claim/process one coordinator artifact intent;
9. bounded activity report and idle wait.

Handlers:

| Handler | Execution boundary |
|---|---|
| Patch/rollback | trusted `FileMutationEngine`; never shell/container writes to real repository; commit-phase cancellation rule |
| Command/subprocess verification | Docker snapshot through `ProjectIsolatedExecutor`; pinned digest, no network, non-root, read-only root, tmpfs, resource/output/time limits |
| Work-unit/repair preparation | coordinator processor reads bounded approved evidence, optionally calls durable local model, writes artifact only |
| Deterministic verification | non-executable pure verifier may run in coordinator; any project code/subprocess becomes an exact approved Docker command |
| Handoff | deterministic artifact builder over canonical evidence; no command execution |

Remove legacy backend selection in 3H. A missing/mismatched image or worker never falls back. Add runtime metrics as bounded persisted counters, not an execution-control side channel. Evidence contains content hash, redaction summary, bounded stdout/stderr, exit/cancellation/timeout/cleanup classification, container identity and effective isolation profile.

## 14. Model, synthesis, agent, and RAG plan

Current truth: `SynthesisGateway` is a provider-neutral protocol with unavailable, fake, and local `OllamaSynthesisGateway` implementations. `build_synthesis_gateway_from_environment()` defaults disabled and uses the selected local SLM profile only when mode is `ollama`. `build_evidence_package()` is bounded to approved workspace excerpts, and strict contracts/prevalidation reject unsafe output. Diagnosis reuses the gateway. Project delivery deliberately reports `rag_used=False`; generic RAG/corpus/dataset capabilities are separate.

Target rules:

- Persist the invocation before calling a provider; lease it; persist the bounded raw-response hash/result; artifact creation reuses it after crash.
- Deterministic synthesis/diagnosis runs first where applicable and never calls a model.
- One coordinator intent/evidence hash/purpose maps to one invocation. Retry of provider infrastructure may be explicit and versioned, never reload-triggered.
- Confidence remains independently calculated from evidence/parser/validation; a model cannot assert its way to approval.
- Evidence includes only approved project/dataset artifacts, with provenance and byte accounting. Project strings are untrusted prompt data.
- Local Ollama is the guaranteed optional provider, not a release dependency. Provider unavailable produces a typed block/clarification.
- No “agent framework” is needed for MVP. The canonical event → intent → handler → artifact → command loop is the bounded agent architecture.
- RAG is optional after core reliability. If introduced, retrieval results become immutable evidence references and cannot add paths, criteria, commands, approval, or verification. Generic corpus indexes are never silently consulted for project mutation.

## 15. Frontend integration plan

The frontend must display, not decide. `CanonicalProjectAction` is a validated projection of `CanonicalProjectResponse`; it does not translate legacy status to canonical lifecycle.

Required behavior:

- Conversation creation/request IDs always come from the backend; keep the current durable pre-stream order.
- Hydration uses `projects` as the current project source, with turns only for conversational chronology.
- Merge one card by `project_run_id`; worker/intent/artifact updates replace that card.
- Buttons come from `next_permitted_actions` and send exact project/state/revision/artifact/idempotency bindings.
- Active queue/execution/cancellation is polled through canonical reads. Browser timers have no authority and never submit work.
- Temporary lookup/network/5xx/409 errors keep the card and identity. Only definitive backend 404 for the project/conversation clears it.
- Historical records use a visibly read-only compatibility parser/card and cannot expose mutation/execution buttons.
- Raw evidence and absolute paths remain hidden/redacted; technical details remain collapsed and bounded.
- Keep one chat product and the current fixed composer/mobile layout; do not add a dashboard.

## 16. Testing and verification matrix

| Matrix | Required coverage |
|---|---|
| Lifecycle | Every `ProjectCommandType` against every source `ProjectLifecycle`: legal success, illegal no-event/no-write, terminal rejection, optimistic conflict, exact idempotent replay/different-payload conflict. |
| Approval | wrong project/conversation/workspace/root/plan/scope/manifest/artifact/hash/type/user; superseded scope/plan; double-click/concurrency; approval invalidation after mutation/rollback/scope. |
| Chat/reload | before stream token, before dispatch, queued, leased, container running, queue terminal, artifact stored, canonical reconciled, projection pending, intent pending/claimed/completed, repair, cancelling, handoff. One identity/invocation/attempt/request. |
| Outbox/worker | crash before/after attempt+dispatch commit, queue enqueue, dispatch ack, lease heartbeat/loss, result evidence, terminal command, reconciliation marker, projection, intent. |
| Cancellation | pre-dispatch, queued, leased before container, running process tree, timeout race, mutation before commit, mutation during commit, cleanup failure, restart. |
| Mutation | traversal/symlink/special files, preimage/result/manifest mismatch, multi-file mid-commit failure, exact replay, rollback, journal corruption/recovery. |
| Isolation | pin mismatch, no Docker, no network, no Docker socket/credentials/proxy, read-only host/root, UID/capabilities/no-new-privileges, PID/CPU/memory/output/time, orphan cleanup. |
| Coordinator/model | claim races, lease expiry, stale binding, budget, provider timeout/unavailable, crash before/after response/artifact/command, duplicate event; one invocation. |
| Verification/repair/handoff | irrelevant success cannot satisfy criterion; stale evidence; manual criteria; domain vs infrastructure failure; one repair; failed repair stop; fresh final manifest; no active work; artifact hash corruption. |
| Migration/compatibility | Stage 0/1/2 fixtures, concurrent startup, interruption, checksum/newer schema, active attempts, legacy approval discard, historical read-only, no GET writes. |
| Frontend | strict normalization, no inference, exact buttons, one-card merge, transient/permanent lookup, stale action, worker unavailable, bounded details, mobile/composer. |
| E2E/benchmark | Python and Node smoke through handoff; 7/8 real repos verified; all 5 stress repos clarify/block/change expected files only; 21/26 repair fixtures within one repair; no baseline regression. |

Unit-only today: most lifecycle, coordinator intent, synthesis/diagnosis, delivery, migration-by-initialize, and frontend parsing behavior. Real integration today: Docker containment, runtime endpoint, Python/Node smoke, timeout/cancellation/reconciliation, API chat/delivery flows using TestClient. Missing E2E: production coordinator processors, terminal-to-next-preview, API cancellation-to-running worker, canonical repair, canonical hydration-only, formal migrations, and full browser automation/manual evidence for all recovery points.

## 17. Security and trust-boundary checklist

- [ ] Backend-issued conversation/request/project/attempt/dispatch/worker/artifact/invocation IDs.
- [ ] Durable request before stream and durable attempt/outbox before work.
- [ ] One lifecycle authority: `ProjectControlPlane.execute()`.
- [ ] Exact immutable plan/scope/manifest/artifact approval binding and invalidation.
- [ ] No frontend lifecycle inference or browser execution authority.
- [ ] No host project-code execution; no legacy subprocess opt-in.
- [ ] No direct host mutation outside `FileMutationEngine`.
- [ ] Container uses disposable snapshot, never writable real-repository mount.
- [ ] Pinned local digest; no pull/build/install during request.
- [ ] Network none, non-root, read-only root, dropped caps, no-new-privileges, tmpfs, resource/output/time limits, no stdin.
- [ ] No Docker socket, VCS metadata, credentials, private keys, tokens, proxy variables or external paths in snapshot/evidence.
- [ ] Path traversal, symlink, special-file, artifact/manifest/preimage changes fail closed.
- [ ] Cancellation reaches exact worker/container and respects atomic mutation commit.
- [ ] Domain failures and infrastructure failures remain distinct.
- [ ] Model/project/RAG input is untrusted; output is strict, bounded, validated, advisory.
- [ ] Evidence is hashed, redacted, bounded, retained/expired explicitly, and corruption blocks handoff.
- [ ] Startup/migration/recovery never re-executes work.
- [ ] Historical compatibility cannot authorize new mutation/execution.

## 18. Documentation and operational plan

Update documentation only when corresponding behavior lands:

- README: current checkpoint, exact backend/worker/frontend startup, environment, capability/doctor commands, no automatic build/pull/install, stop procedure.
- **NEW** `docs/astra-local-operations.md`: WSL2/Docker prerequisites, database backup/migration, runtime image build/load/digest verification, worker identity/heartbeats, recovery/cancellation/projection diagnostics, evidence retention, shutdown/orphan inspection.
- Stage documents: retain Stage 0–2C as immutable checkpoint history; add one file per Stage 3 checkpoint only after gates pass.
- `docs/FINAL_SYSTEM_STATUS.md`: mark historical and point to README/current checkpoint. Do not call it current.
- `docs/MIGRATION.md` and cleanup reports: preserve as history and correct active-runtime links.
- Operator diagnostics must report state without auto-repair. Recovery commands require explicit invocation and must never re-execute an attempt.

## 19. Stage-by-stage Codex execution prompts

Each prompt is ready to paste into a fresh Codex session. Before implementation, the session must read this master plan and verify the current checkpoint. No prompt authorizes commit/push unless the user separately requests it.

### Prompt for Stage 3A

> Implement only Stage 3A from `docs/astra-remaining-implementation-master-plan.md`: versioned migration and contract foundation. Begin with ownership/baseline tests. Add the files and symbols marked NEW for migrations, project artifacts, project model invocations, and execution cancellation contracts. Convert existing service initialization to the reviewed compatibility path without changing product workflow. Preserve all Stage 0–2C guarantees and the existing untracked/user changes. Run only the Stage 3A narrow commands first, then existing control/worker initialization regressions. Do not implement Stage 3B+, do not stage/commit/push, and report migration fixtures, test results, diff/status, and any plan deviation.

### Prompt for Stage 3B

> Implement only Stage 3B from `docs/astra-remaining-implementation-master-plan.md` on top of accepted Stage 3A. Create canonical project/artifact services and typed project API. New project creation must persist `ProjectRun`, exact scope/manifest/plan artifacts, revisions and events before compatibility projection; GET must be side-effect-free. Keep historical delivery read/reapproval support. Add exact API/service/artifact tests including refresh during creation. Do not implement worker follow-on, repair, or frontend card replacement. Run the listed Stage 3B checks; do not stage/commit/push.

### Prompt for Stage 3C

> Implement only Stage 3C from the master plan. Make terminal worker reconciliation, cancellation delivery/acknowledgement, compatibility projection and next-intent creation converge idempotently. Queue rows and projectors must never write canonical lifecycle directly. A running cancellation must stop the exact worker/container before canonical terminal cancellation; mutation commit recovery remains atomic. Remove GET-side mutation. Add every specified crash/cancellation/reconciliation test, including Docker-gated cases. Do not add coordinator artifact generation or repair. Do not stage/commit/push.

### Prompt for Stage 3D

> Implement only Stage 3D. Add production coordinator intent processing for work-unit preparation, deterministic verification and handoff in the separate project worker. Processors may create immutable artifacts and submit canonical commands only; they cannot approve, mutate or run project code on host. Automatically prepare the next preview, but retain exact patch/command approvals. Make model invocation/reload idempotent and replace opaque two-step handoff finalization for new records. Add claim/crash/reload/multi-work-unit tests. Do not implement repair or RAG. Do not stage/commit/push.

### Prompt for Stage 3E

> Implement only Stage 3E. Add one canonical repair cycle: bounded worker failure artifact, deterministic-first diagnosis, at most one repair preview, exact user approval, queued mutation, and fresh verification. Infrastructure failures must block without a repair preview. Scope expansion must revise scope/plan and invalidate approval. A failed repair verification must stop until explicit user action. Migrate presentation away from legacy job repair authority and add the specified recovery/limit/rollback tests. Do not stage/commit/push.

### Prompt for Stage 3F

> Implement only Stage 3F. Add hydration v2 and the canonical typed frontend state/card. New records must render solely from canonical project responses and backend `next_permitted_actions`; move legacy lifecycle inference into a clearly tagged historical parser. Reload/polling must never submit work, temporary lookup failures must preserve identity, and only definitive 404 clears it. Keep one chat UI and mobile composer. Run backend contract tests plus all named frontend/TypeScript/lint/build checks. Do not stage/commit/push.

### Prompt for Stage 3G

> Implement only Stage 3G after the canonical loop is accepted. Add durable idempotent synthesis orchestration, expanded bounded multi-file evidence, strict full-tree prevalidation, and typed toolchain/dependency capability. Keep Ollama optional/provider-neutral and model output advisory. Do not install dependencies, enable runtime network, add arbitrary images, or add a multi-agent framework. Project RAG is optional and disabled unless provenance/scope/prompt-injection tests are implemented. Run the narrow synthesis/analysis/benchmark checks. Do not stage/commit/push.

### Prompt for Stage 3H

> Implement only Stage 3H. Retire all reachable host project-code execution and direct host project mutation compatibility, restrict legacy adapter/routes to historical read/import/reapproval, add read-only doctor/startup operations, correct active documentation, and run the complete release gates. Prove historical records remain readable and new canonical records never fall back. Run full backend, Docker, frontend, lint/build, browser/manual, benchmark, diff/status, and process/container cleanup checks. Do not stage/commit/push unless the user separately authorizes it.

## 20. Final production-readiness criteria

Astra is a credible local MVP only when all criteria below are true together:

1. One canonical project lifecycle authority exists in code and tests; no new route, worker, projector, frontend reducer, delivery/job status, or model output can bypass it.
2. Every new project, artifact, approval, attempt, dispatch, worker request, model invocation, result and cancellation has backend durable identity and exact bindings.
3. All restart/reload/crash points converge without a second model invocation, attempt, worker request, container, mutation, result, coordinator intent, or card.
4. Queue terminal results automatically and idempotently produce canonical state, current projection and the correct next coordinator intent.
5. Cancellation stops exact queued/running work and converges safely, including mutation commit recovery.
6. The approval-controlled happy path completes small-to-medium Python and Node projects through multi-work-unit handoff.
7. The failure path performs at most one bounded repair proposal, requires approval, re-verifies freshly, and stops after failure without explicit user action.
8. Verification/handoff use only current artifact-bound evidence and a fresh final manifest; mandatory/manual criteria are treated explicitly.
9. Browser hydration uses one canonical project collection/card and never infers authority or deletes valid state after transient failure.
10. Formal migrations upgrade all supported checkpoint fixtures restart-safely; unknown/corrupt/newer schemas fail closed; backup/restore is documented.
11. No reachable new or historical project chat action executes repository code on host or mutates files outside `FileMutationEngine`.
12. Docker security and cleanup guarantees continue to pass real integration tests; missing capability has no fallback.
13. Model evidence, output, confidence, invocation and optional RAG provenance are bounded, redacted, immutable and non-authoritative.
14. Operator startup, capability, reconciliation, cancellation, migration and cleanup diagnostics are documented and do not perform implicit repair/build/pull/install.
15. Full backend/frontend/Docker/lint/build/diff gates pass, browser reproductions pass, no managed container/process remains, and benchmark gates meet: at least 7/8 real repositories verified, all 5 stress repositories safe, at least 21/26 one-repair fixtures successful, Python and Node smoke through handoff, and zero identity/approval/duplicate/out-of-scope/security violations.

The single recommended next implementation stage is **Stage 3A — versioned migration and contract foundation**. It is the smallest safe next checkpoint and is a prerequisite for every durable integration that follows.
