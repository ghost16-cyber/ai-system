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

Start FastAPI in one terminal:

```bash
export AI_SYSTEM_DB_PATH=data/app/ai_system.db
export AI_SYSTEM_WORKSPACE_ROOT="$PWD"
uvicorn backend.app.main:app --reload
```

The runtime image must already exist locally. Configure its observed digest,
not a mutable tag alone:

```bash
docker image inspect astra-project-runtime:stage2c-v1 --format '{{.Id}}'
export ASTRA_PROJECT_EXECUTION_BACKEND=docker
export ASTRA_PROJECT_RUNTIME_IMAGE=astra-project-runtime:stage2c-v1
export ASTRA_PROJECT_RUNTIME_IMAGE_DIGEST=sha256:<observed-image-id-or-digest>
```

Start the separate worker in a second terminal with the same database and
workspace settings:

```bash
python -m backend.app.project_workers
```

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
python -m pytest -m docker_integration -q

git diff --check
git status --short
git diff --stat
```

On WSL, prefix pytest with `TMP=/tmp TEMP=/tmp` if the shell inherited Windows
temporary-directory variables.

## Current Docker integration blocker

At the 2026-07-20 stabilization checkpoint, Docker Desktop and the engine are
healthy, but `astra-project-runtime:stage2c-v1` does not exist locally and the
repository contains neither its approved Dockerfile/build context nor tests
marked `docker_integration`. Therefore the containment suite cannot truthfully
be reported as passing. `python -m pytest -m docker_integration -q` selects no
tests and exits with pytest code 5.

To unblock the gate, add or obtain the reviewed runtime image build source,
build it outside request processing, inspect its digest with the command above,
configure `ASTRA_PROJECT_RUNTIME_IMAGE_DIGEST`, and add the gated containment
cases before rerunning the marker. Do not substitute an unrelated local image.

## Known limitations

- The automatic next-work-unit and one-repair loop is not part of this
  checkpoint. Coordinator intents are durable infrastructure only.
- Coordinator artifact-processing workers and expanded multi-file synthesis
  evidence are intentionally deferred.
- Docker containment, isolated Python/Node smoke flows, and their startup
  benchmarks remain blocked by the missing approved runtime image and missing
  gated integration cases.
- The general chat `Run the tests` compatibility action still uses the legacy
  host command path; it is not evidence that canonical project execution used
  Docker. Canonical project route tests remain queued and fail closed.
- Distributed/cloud execution, team collaboration, request-time dependency
  installation, arbitrary user images, and automatic approval are out of scope.

See `docs/stabilization-checkpoint.md` for the exact regression, API, browser,
recovery, and benchmark evidence for this working tree.
