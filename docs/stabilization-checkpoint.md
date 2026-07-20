# Working-tree stabilization checkpoint - 2026-07-20

This report records verification of the uncommitted Stage 2C working tree on
branch `feature/chat-native-approval`. Nothing was staged, committed, or pushed.

## Regression results

| Gate | Result |
| --- | --- |
| Python compilation | Passed: `python -m compileall -q backend/app tests`. |
| Complete backend | Passed: 924 passed, 0 failed, 0 skipped in 340.63 seconds. |
| Frontend tests | Passed: 87 passed, 0 failed, 0 skipped/cancelled/todo. |
| Frontend lint | Passed: ESLint completed with no findings. |
| Frontend production build | Passed: 1,589 modules; JS 289.13 kB (81.28 kB gzip), CSS 20.72 kB (4.83 kB gzip), build 1.51 seconds. |
| Diff whitespace | Passed before documentation update; rerun in final integrity inspection. |
| Docker integration | Blocked: Docker is healthy, but the pinned image and repository Docker integration cases are absent. |

Docker Desktop 4.56.0 and Engine 29.1.3 were available through WSL2. The
configured `astra-project-runtime:stage2c-v1` image was not present. The runtime
capability endpoint returned `available=false`, `failure_code=image_unavailable`,
and `host_execution_fallback=false`. The marker command selected 0 tests and
exited with code 5; it is not counted as a pass.

## Worker CLI and recovery

`python -m backend.app.project_workers --once --dispatch-only` initialized a
fresh temporary database and exited 0 after exactly one cycle. Its JSON output
contained no lease token. Docker mode exited non-zero with
`image_unavailable` and did not fall back to host execution. A focused CLI,
dispatch, mutation, coordinator, and API recovery slice passed 40 tests in
19.18 seconds. A second infrastructure timing slice passed 32 tests in 13.37
seconds.

Crash boundaries converge as follows:

| Boundary | Deterministic evidence | Result |
| --- | --- | --- |
| Before enqueue | Attempt and dispatch persist before queue delivery. | One durable attempt/outbox. |
| After enqueue, before outbox acknowledgement | Enqueue replay reuses the same worker request. | No duplicate request. |
| After dispatch binding | Queue restart and repeated dispatch preserve binding. | Same IDs. |
| Worker success before canonical reconciliation | Queue-only completion recovery reconciles success. | No re-execution. |
| Patch mutation before completion recording | Journal recovery and exact replay recognize the applied result. | At most one mutation. |
| Rollback mutation before completion recording | Combined queued patch/rollback worker and replay recovery preserve exact preimages. | At most one restore. |
| Coordinator lease expiry | Expired claim recovery is durable. | Intent becomes claimable once. |
| Duplicate coordinator execution | Reconciliation is idempotent and budgeted. | One intent/result. |
| Patch projection after completion | Delivery API reload projects succeeded mutation evidence. | Stable canonical IDs. |
| Rollback projection after completion | Delivery API reload projects `rolled_back`. | Stable canonical IDs. |

## API reproduction

| Flow | Endpoint/state | Expected | Observed |
| --- | --- | --- | --- |
| Queued command | `POST /chat/projects/commands/{plan_id}/execute` after exact approval | HTTP 200, `queued`, pending dispatch | Passed; replay retained the same attempt and dispatch IDs. |
| Subprocess verification | `POST /chat/projects/deliveries/{id}/verification`, then command approve/execute | Exact spec becomes queued command execution | Passed in delivery API regression. |
| Queued patch | `POST /chat/projects/patches/{patch_id}/apply` after exact approval | Pending dispatch, no inline repository write | Passed; worker completion projected mutation evidence. |
| Queued rollback | `POST /chat/projects/rollback/request` and `/rollback/{patch_id}/approve` | Pending rollback, exact restore once | Passed; reload projected `rolled_back`. |
| Cancellation | `POST /chat/projects/deliveries/{id}/cancel` | Cancelled and idempotent | HTTP 200 twice, same delivery ID and `cancelled` status. |
| Reload while queued | `GET /chat/projects/deliveries/{id}` | Same attempt/dispatch/request IDs | Passed. |
| Reload after completion | Same delivery GET | Terminal result and evidence retained | Passed for patch and rollback. |
| Duplicate submission | Repeated execute body/idempotency key | No second attempt/request | Passed: one attempt and one worker request. |
| Runtime capability | `GET /chat/projects/runtime-capabilities` | Truthful worker/isolation health | HTTP 200; worker unavailable, image unavailable, no host fallback. |
| Coordinator progress | `GET /chat/projects/deliveries/{id}` | Durable intent returned without retrigger | Passed; repeated read retained the coordinator intent ID. |

The two end-to-end delivery API reproductions passed in 1.73 seconds (patch,
verification, reload, duplicate execution) and 1.85 seconds (rollback and
projection), excluding test setup/teardown.

## Manual browser reproduction

The verified production bundle was served locally and connected to the real
FastAPI process. A temporary WSL-origin CORS entry was used only for the test and
removed afterward.

- An approval card survived reload with one card and no resubmission.
- After a terminated compatibility command, reload retained one failed card
  with exit code -15 and did not create another process.
- Cancelling before approval remained visible as `cancelled` and stated that no
  command was executed.
- Technical evidence was collapsed by default.
- No obsolete dashboard or duplicate action card appeared.
- At 390 x 844, the cancelled card and composer were visible and the composer
  remained usable.
- A complete canonical queued -> leased/running -> terminal Docker flow, patch
  projection, and rollback projection could not be reproduced in-browser
  because the pinned runtime image/worker was unavailable. Those projections
  passed at the API level.
- The general-chat `Run the tests` card launched the legacy host executor. The
  reproduction process was terminated after about 21 seconds; this path must
  not be confused with canonical project execution.

## Bounded benchmark evidence

Measurements are local single samples or focused deterministic test durations;
they are evidence, not a performance acceptance declaration.

| Measurement | Result |
| --- | --- |
| Worker queue enqueue | 6.466 ms |
| Worker claim | 5.654 ms |
| Queue restart initialize plus exact request read | 0.716 ms, same request ID |
| Multi-file exact mutation and replay unit | 0.28 s |
| Interrupted mutation restart recovery unit | 0.15 s |
| Queued patch plus rollback canonical reconciliation | 0.76 s combined |
| Patch API completion flow | 1.73 s |
| Rollback API completion flow | 1.85 s |
| Output bound | 1,048,576-byte total policy; 523,776 bytes per stream in the tested split; excess input set `truncated=true`. |
| Dispatch-only idle worker | 6.1% CPU average and 551,768 KiB RSS after 33 seconds; 63 half-second cycles; no work claimed. |
| Docker startup | Blocked by missing pinned image. |
| Python isolated verification | Blocked by missing pinned image. |
| Node isolated verification | Blocked by missing pinned image. |

The idle memory/CPU sample is notably high for an idle local worker and is a
future optimization target; this checkpoint intentionally does not optimize it.

## Checkpoint assessment

The code-level checkpoint gates are satisfied: compilation, the complete
backend suite, frontend tests/lint/build, idempotent dispatch/recovery, and exact
mutation recovery pass. Docker containment is an explicitly documented
external/repository-fixture blocker, not a passing gate. No unrelated file was
staged. Automatic repair remains intentionally absent.

Subject to the final clean diff/status inspection, this working tree is safe to
checkpoint as incomplete Stage 2C infrastructure. It is not yet safe to call a
complete credible local MVP or to make Docker execution the default release
until the pinned image and containment suite exist and pass.
