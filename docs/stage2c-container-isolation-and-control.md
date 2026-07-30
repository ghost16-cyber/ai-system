# Stage 2C - Container Isolation and Durable Local Control

Stage 2C moves new canonical project execution behind a separate durable worker
and a fail-closed Docker backend. `ProjectControlPlane` is the only authority
that may advance the canonical lifecycle.

## Ownership map

| Concern | Owner | Authority boundary |
| --- | --- | --- |
| Project lifecycle, attempts, approvals, and terminal results | `ProjectControlPlane` | Queue rows and workers never write lifecycle state directly. |
| Attempt-to-queue delivery | Transactional execution outbox | The outbox is committed with the attempt and is replayed idempotently. |
| Worker leases, heartbeats, cancellation acknowledgement, and terminal payloads | `ProjectWorkerQueue` | Queue state is operational state, not canonical project authority. |
| Typed background requests and budgets | Project coordinator intents | The coordinator requests work but cannot approve it or mutate lifecycle state. |
| Exact repository mutation | Trusted host-side `FileMutationEngine` | Containers never receive a writable mount of the real repository. |
| Command and subprocess execution | `IsolationBackend` worker handler | New canonical execution fails closed when isolation is unavailable. |

## Isolation guarantees

The Docker profile uses a pre-existing local image pinned by digest. Request
processing never pulls, builds, or installs dependencies. Each execution gets a
disposable workspace snapshot and runs as non-root with networking disabled, a
read-only root filesystem, all Linux capabilities dropped,
`no-new-privileges`, bounded PIDs/CPU/memory/output/wall time, tmpfs temporary
storage, no stdin, and no Docker socket, host credentials, proxy variables, or
VCS metadata. Repository identity, paths, artifact hashes, the image digest,
and the execution-spec hash are revalidated before container creation.

Missing Docker, an unavailable or mismatched image, containment failure,
corrupt evidence, or cleanup failure blocks the attempt. There is no
unsandboxed fallback for canonical project execution.

Stage 3H retires the old `ProjectSubprocessExecutor` runtime selection and all
reachable compatibility-route calls to direct host patch/rollback helpers.
`ASTRA_ALLOW_LEGACY_PROJECT_EXECUTION` is ignored. Canonical mutation remains a
separately approved, journaled `FileMutationSpec`; arbitrary compatibility
records cannot invoke it.

## Patch and rollback recovery

Patch and rollback approvals bind an immutable `FileMutationSpec` to the exact
plan, scope, manifest, paths, preimages, result hashes, and operation order. The
host mutation engine stages all outputs, records durable snapshots and a
journal, atomically replaces individual files, rolls the complete file set back
on failure, recognizes exact replay, and recomputes the repository manifest.
Cancellation is accepted before commit. Once commit begins, restart recovery
finishes or restores the journal deterministically rather than interrupting
between files.

## Runtime startup

The reviewed combined Python/Node build context is
`docker/stage2c-runtime/`. Its immutable official base is:

```text
node@sha256:6c74791e557ce11fc957704f6d4fe134a7bc8d6f5ca4403205b2966bd488f6b3
```

Build and load the exact local configuration before starting either process:

```bash
./scripts/build_stage2c_runtime.sh
source ./.astra-stage2c-runtime.env
```

The validated build produced:

```text
astra-project-runtime:stage2c-v1
sha256:48e704e4391a936154583148f8d7950a1a15216bf38c8f4a57f153401a7bab2c
```

The digest is build output, not a permanent alias. After an intentional image
change, rebuild and repin with the script; never hand-edit a tracked secrets or
environment file. `scripts/load_stage2c_runtime.sh` can also print or export the
generated values safely.

Start FastAPI in one terminal:

```bash
export AI_SYSTEM_DB_PATH=data/app/ai_system.db
export AI_SYSTEM_WORKSPACE_ROOT="$PWD"
uvicorn backend.app.main:app --reload
```

Start the separate worker in a second terminal with the same database and
workspace settings:

```bash
python -m backend.app.project_workers
```

Alternatively, `scripts/run_local_astra.sh` validates the already provisioned
runtime and pinned image, records explicit PID/log files, and stops all three
local processes through one signal trap. It never installs, builds, or pulls.

Useful bounded modes are:

```bash
python -m backend.app.project_workers --once
python -m backend.app.project_workers --once --dispatch-only
python -m backend.app.project_workers --help
```

FastAPI reports, but does not own, the worker loop at
`GET /chat/projects/runtime-capabilities`.

## Verification commands

```bash
python -m compileall -q backend/app tests
python -m pytest -q

cd frontend
node --test --experimental-strip-types tests/*.test.ts
npm run lint
npm run build
cd ..

docker version
docker info
source ./.astra-stage2c-runtime.env
python -m pytest -m docker_integration -q

git diff --check
git status --short
git diff --stat
```

On WSL, prefix pytest with `TMP=/tmp TEMP=/tmp` if the shell inherited Windows
temporary-directory variables.

## Validated Docker integration

The real-container suite executes Python and Node smoke projects and validates
image probing, non-root identity, network denial, read-only root operation,
tmpfs and snapshot policy, zero capabilities, `NoNewPrivs`, resource limits,
bounded/redacted output, timeouts, cancellation, restart convergence, orphan
cleanup, runtime reporting, and cleanup of container-owned cache directories.

At this checkpoint the marker selected and passed 19 real Docker tests. The
complete backend suite passed 946 tests after the live cleanup defect was added
as a regression case. See `docs/stabilization-checkpoint.md` for exact commands
and evidence.

## Known limitations

- The automatic next-work-unit and one-repair loop is not part of this
  checkpoint. Coordinator intents are durable infrastructure only.
- Coordinator artifact-processing workers and expanded multi-file synthesis
  evidence are intentionally deferred.
- The reviewed image includes only its declared Python and Node tools. Projects
  that need undeclared dependencies fail safely; request-time installation and
  arbitrary images remain prohibited.
- A connected general-chat `Run the tests` action without a canonical delivery
  binding returns a controlled failure and never launches host pytest. Commands
  attached to a canonical delivery use approval, outbox, queue, worker, and
  Docker execution. Historical Stage 6 records retain their governed recovery
  compatibility during this release.
- Snapshot permission repair uses a second tightly constrained, non-root helper
  container running the same pinned image and UID. Failure to restore removable
  permissions remains a fail-closed infrastructure result.
- Distributed/cloud execution, team collaboration, request-time dependency
  installation, arbitrary user images, and automatic approval are out of scope.

See `docs/stabilization-checkpoint.md` for the exact regression, API, browser,
recovery, and benchmark evidence for this working tree.
