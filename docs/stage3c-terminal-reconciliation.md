# Stage 3C terminal reconciliation checkpoint

Stage 3C makes durable queue completion and cancellation converge into the
canonical project control plane exactly once. It does not add coordinator
processors, autonomous work-unit generation, repair generation, or synthesis.

## Ownership

- `ProjectControlPlane` remains the only writer of canonical project lifecycle.
- `TerminalResultReconciler` converts a terminal queue row into one immutable
  result artifact and one idempotent canonical command.
- `CancellationDispatcher` delivers an exact canonical cancellation row to the
  existing worker request and waits for terminal worker acknowledgement before
  submitting canonical terminal cancellation.
- `ProjectProjectionService` writes only compatibility delivery/chat read state
  and its projection checkpoint. A projection error pauses that checkpoint and
  never rewrites canonical state.
- `ProjectCoordinatorService.reconcile_all()` repairs missing idempotent intent
  creation only; it does not claim or process an intent.

## Worker recovery order

Each bounded worker cycle performs cancellation delivery, transactional-outbox
dispatch, expired-lease and terminal-result reconciliation, cancellation
acknowledgement, compatibility projection, and missing-intent reconciliation
before it may claim new execution work. Recovery never re-executes a terminal
worker request.

FastAPI initializes and reports the stores but does not own that recovery or
execution loop. Start the separate worker with:

```bash
source ./.astra-stage2c-runtime.env
export AI_SYSTEM_DB_PATH=data/app/ai_system.db
export AI_SYSTEM_WORKSPACE_ROOT="$PWD"
python -m backend.app.project_workers
```

The Docker image must already exist locally and
`ASTRA_PROJECT_RUNTIME_IMAGE_DIGEST` must contain its inspected digest. Astra
does not pull, build, install, or fall back to host execution while processing a
request.

## Focused verification

```bash
TMP=/tmp TEMP=/tmp .venv/bin/python -m pytest -q \
  tests/test_project_terminal_reconciliation.py \
  tests/test_project_projection.py \
  tests/test_project_cancellation.py \
  tests/test_project_worker_dispatch.py \
  tests/test_project_workers.py \
  tests/test_project_mutation_worker.py

TMP=/tmp TEMP=/tmp .venv/bin/python -m pytest -q -m docker_integration \
  tests/test_project_worker_docker_integration.py \
  -k 'cancellation or reconciliation or restart'

git diff --check
```

The Docker-gated cases skip when the pinned digest is not configured; a skip is
not acceptance evidence. For a controlled rollback, stop workers, settle all
in-flight cancellation rows, then set
`ASTRA_PROJECT_RECONCILIATION_ENABLED=0`. Never enable legacy host fallback.
