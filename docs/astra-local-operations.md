# Astra canonical local operations

This runbook covers the Stage 3H local MVP. It assumes Python, Node/npm, Docker,
the Python virtual environment, frontend dependencies, and the pinned
`astra-project-runtime:stage2c-v1` image already exist. None of the commands in
this runbook install packages, pull/build images, download models, or train.

## Ownership and safety boundary

`ProjectControlPlane` alone advances lifecycle state. The coordinator prepares
bounded work, the model invocation store provides provider-neutral idempotency,
the worker queue owns leases only, the terminal reconciler submits exact
canonical results, and the projector is read-only. Every patch, rollback, and
subprocess command requires a separate exact user approval. Browser state,
legacy records, queue rows, workers, and models are never lifecycle authority.

Project code runs only in the pinned Docker isolation profile. Direct host
project execution is retired. Direct legacy patch/rollback helpers are not
reachable from project chat routes. Historical records remain queryable but
return `historical_record_read_only` for mutation attempts.

## Preflight doctor

Run the read-only doctor before startup:

```bash
.venv/bin/python scripts/astra_project_doctor.py \
  --database-path data/app/ai_system.db
```

It reports the schema ledger, pending migrations, worker heartbeat, pinned image
and digest, pending queue/coordinator/cancellation/projection counts, historical
read-only records, and bounded failure classifications. It never repairs,
builds, pulls, or starts a container. Use `--no-docker` only for database-only
diagnosis; that mode cannot declare Docker startup safe.

An existing database is copied with SQLite's backup API to
`<database>.pre-stage3h-v8.bak` before the Stage 3H historical classification
migration. Keep that backup until the migration fixtures and historical reads
have been verified.

When the doctor reports pending migrations, apply only the reviewed additive
registry, then rerun the doctor:

```bash
.venv/bin/python scripts/migrate_astra_database.py \
  --database-path data/app/ai_system.db
```

The command reports the exact versions applied and the backup path. It performs
no dependency installation, image pull, model access, training, or application
startup. Unknown, corrupt, checksum-mismatched, or newer ledgers fail closed.

## Start and stop

With the existing Node environment on `PATH`:

```bash
./scripts/run_local_astra.sh
```

The script validates the doctor and existing `frontend/node_modules`, then
starts FastAPI, the canonical Docker worker, and Vite. PID and log files are
stored under `data/runtime/` by default. Override only explicit paths/ports with
`ASTRA_RUNTIME_DIR`, `AI_SYSTEM_DB_PATH`, `ASTRA_BACKEND_PORT`, or
`ASTRA_FRONTEND_PORT`. The checked-in launcher reads the already-provisioned
`.astra-stage2c-runtime.env` when present; override its location with
`ASTRA_RUNTIME_ENV_FILE`.

Press Ctrl-C once. The trap terminates all recorded child processes, waits for
them, and removes PID files. Inspect `docker ps --filter name=astra-project-`
after abnormal shutdown; do not delete unrelated containers. The worker's
normal startup performs bounded orphan cleanup only for Astra-managed names.

## Typed blocked states

- `provider_unavailable`: no model preview was created; deterministic preparation remains available.
- `image_unavailable` / `image_digest_mismatch`: execution remains queued or blocked; there is no host fallback.
- `historical_record_read_only`: import/reapproval is required before canonical mutation.
- `stale_state_version`, revision/hash/binding mismatch: reload the canonical card and review the new exact action.
- `project_rag_disabled`: generic RAG cannot become project evidence authority.
- unsupported runtime/dependency: the toolchain preflight blocks and never installs automatically.

## Validation and recovery

The release gate is the full backend suite, Docker integration suite, all
frontend contract tests, ESLint, production build, Python compilation,
migration fixtures, Python/Node handoff journeys, `git diff --check`, and a final
zero-managed-process/container inspection. Restart recovery must not resend a
model invocation, worker request, terminal result, cancellation, or browser
action. A failed single repair stops for explicit scope revision or a new cycle.
