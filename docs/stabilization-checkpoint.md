# Stage 2C Docker runtime validation checkpoint - 2026-07-20

> Historical checkpoint. Stage 3H retains this exact pinned image but retires
> legacy host execution and adds the canonical operator path documented in
> [`astra-local-operations.md`](astra-local-operations.md).

This report records verification of the uncommitted Stage 2C working tree on
branch `feature/chat-native-approval`. Nothing was staged, committed, or pushed.

## Runtime image

| Item | Validated value |
| --- | --- |
| Build context | `docker/stage2c-runtime/` |
| Image tag | `astra-project-runtime:stage2c-v1` |
| Immutable official base | `node@sha256:6c74791e557ce11fc957704f6d4fe134a7bc8d6f5ca4403205b2966bd488f6b3` |
| Built image ID | `sha256:48e704e4391a936154583148f8d7950a1a15216bf38c8f4a57f153401a7bab2c` |
| Runtime user | `65532:65532` |
| Toolchains | Python 3.11 with pytest; Node 22 with npm and `node --test` |

Build and load the image outside request processing:

```bash
./scripts/build_stage2c_runtime.sh
source ./.astra-stage2c-runtime.env
```

The generated `.astra-stage2c-runtime.env` is local, mode-restricted, and
ignored by Git. The build script validates the exact lowercase SHA-256 image ID
before writing it and refuses to overwrite a tracked environment file.

## Regression results

| Gate | Result |
| --- | --- |
| Python compilation | Passed: `python -m compileall -q backend/app tests`. |
| Focused isolation/dispatch/CLI/recovery slice | Passed: 46 tests in 24.29 seconds before the live cleanup regression; the added cleanup slice passed 3 unit tests in 8.36 seconds. |
| Real Docker integration | Passed: 19 selected tests, 0 skipped after the image was configured. |
| Complete backend | Passed: 946 tests, 0 failures in 317.44 seconds. |
| Frontend tests | Passed: 87 passed, 0 failed, 0 skipped/cancelled/todo. |
| Frontend lint | Passed: ESLint completed with no findings. |
| Frontend production build | Passed: 1,589 modules; JS 289.13 kB (81.28 kB gzip), CSS 20.72 kB (4.83 kB gzip), build 1.63 seconds. |
| Diff whitespace | Passed in final integrity inspection. |

The full backend and 19-test Docker totals are the final post-fix gates. Earlier
in the session the 18-test Docker suite and 944-test backend suite passed before
the live browser flow exposed a container-owned pytest cache cleanup defect.
That defect is now fixed and represented by the nineteenth Docker regression.

## Real containment evidence

- `DockerIsolationBackend.probe()` returned `available=true`, no failure code,
  and equal configured/observed image IDs.
- Missing image, malformed or wrong digest, and unavailable Docker cases failed
  closed without invoking a host executor.
- Container identity was UID/GID `65532:65532`; `NoNewPrivs` was `1` and
  effective Linux capabilities were zero.
- Docker inspection confirmed `--network none`, read-only root, all
  capabilities dropped, no-new-privileges, fixed non-root user, and configured
  PID, memory, and CPU limits.
- External TCP and DNS access failed. `/tmp`, `/home/astra`, and only the
  disposable `/workspace` snapshot were writable.
- The real repository was not mounted and remained unchanged by container code.
- VCS metadata, Docker socket, credentials, private keys, proxy variables, and
  host tokens were absent. Persisted stdout/stderr were bounded and redacted.
- Excessive child creation was bounded by the PID limit.
- Python pytest/script and dependency-free Node test/npm pass and fail cases
  produced typed domain results.

Pytest creates cache directories owned by the fixed container UID. The live
flow found that mode-0700 cache content could prevent host deletion even after a
successful test. Cleanup now runs a second pinned-image helper as the same
non-root UID with network disabled, read-only root, dropped capabilities,
no-new-privileges, and tight PID/CPU/memory limits. It changes permissions only
on snapshot entries owned by UID 65532; host-owned source files are not changed.
Cleanup failure still blocks the attempt.

## Timeout, cancellation, and restart convergence

The real timeout fixture returned `timed_out`, force-removed its container, and
left no managed container. The real cancellation fixture returned `cancelled`,
completed one lease and one terminal event, removed the container, and did not
re-execute after queue/service restart.

Crash-boundary tests converge to one canonical attempt, one dispatch, one worker
request, one terminal result, and zero remaining containers. Repeated dispatch
does not enqueue again; success recorded in the queue before canonical
reconciliation is recovered after restart without executing the command twice.
Orphan cleanup removes only Astra-labelled containers outside the exact active
identity set.

## Runtime capability result

With the backend and separate worker running, the live endpoint reported:

```text
execution_backend=docker
worker_available=true
active_worker_count=1
isolation_capability.available=true
isolation_capability.failure_code=null
configured_digest=observed_digest=sha256:48e704e4391a936154583148f8d7950a1a15216bf38c8f4a57f153401a7bab2c
supported_toolchains=[python,node]
host_execution_fallback=false
```

## Manual browser reproduction

The Vite UI, real FastAPI backend, separate worker, and Docker Desktop engine
were run together against a disposable four-file project and temporary SQLite
database. A temporary exact WSL-origin CORS entry was used only during the test
and removed before final verification.

- Folder authorization and read-only scan completed in chat.
- The delivery plan required exact approval; patch preparation changed no file.
- Exact patch approval created a pending canonical dispatch. The worker claimed
  it once, applied the host-side mutation, and reload showed the same succeeded
  worker identity.
- Exact `python -m pytest -q test_app.py` approval created a second pending
  dispatch. Docker executed it successfully and reload showed its canonical
  worker result.
- Reload preserved the conversation, project, delivery, and canonical IDs.
- Delivery cancellation became a durable visible `cancelled` state.
- A connected general-chat `Run the tests` card required approval and then
  failed closed with the explicit message that no project code ran on the host
  because it lacked a canonical isolated binding.
- Process inspection immediately afterward found no host pytest process.

The live run initially exposed the snapshot cleanup defect described above. A
fresh worker using the fix completed the same pytest path successfully. Manual
duplicate-token replay could not be repeated because approval tokens are
one-time secrets and are intentionally not persisted in plaintext; duplicate
submission convergence is instead covered by the API and real restart tests.

## Idle worker measurement

| Measurement | Before | After |
| --- | ---: | ---: |
| Sample duration | 33 seconds | 40 seconds |
| CPU | 6.1% | 5.2% |
| RSS | 551,768 KiB | 552,204 KiB |
| Identical idle report lines | 63 | 1 |

The default poll interval is now one second and is clamped to at least one
second. Runtime heartbeat persistence is throttled to five seconds and
unchanged idle reports are not printed. CPU improved modestly; RSS did not and
is reported without qualification as a remaining optimization target.

## Remaining limitations

- Request-time dependency installation and arbitrary images are prohibited.
  Projects needing libraries absent from the reviewed image fail safely.
- Only Python and Node are guaranteed initial execution profiles.
- Automatic repair, coordinator artifact processing, provider-neutral routing,
  expanded synthesis, cloud execution, Kubernetes, Celery, and Redis remain out
  of this checkpoint.
- Historical Stage 6 records retain governed compatibility for recovery, but a
  new connected canonical project never falls back to host execution once a
  canonical attempt exists.
- The browser delivery projection shows the canonical terminal worker result,
  while follow-on legacy delivery criteria still require their normal explicit
  transition; Stage 2C does not add automatic coordinator artifact processing.

## Checkpoint assessment

The reviewed image exists, real containment and smoke flows pass, the canonical
queue/worker path is durable, the general-chat host escape is closed, and no
managed container remains. Subject to the final status/diff inspection, this
working tree is safe to checkpoint as completed Stage 2C Docker runtime and real
containment validation. It is not yet the complete autonomous local MVP.
