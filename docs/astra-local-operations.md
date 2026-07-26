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

## Local AI readiness and advisory smoke

`LocalAIService` is the canonical authority for local-model configuration,
readiness, GPU/RAM/VRAM admission, exclusive scheduling, structured response
validation, idempotency, and provenance. Chat and canonical project synthesis
both use that service. Model output is advisory only and cannot approve,
execute, verify, roll back, or advance a project lifecycle.

Check an already-running Ollama instance without starting it or downloading
anything:

```bash
OLLAMA_ENDPOINT="${ASTRA_OLLAMA_ENDPOINT:-http://127.0.0.1:11434}"
curl --fail --silent --show-error --max-time 3 "$OLLAMA_ENDPOINT/api/version"
curl --fail --silent --show-error --max-time 3 "$OLLAMA_ENDPOINT/api/tags"
curl --fail --silent --show-error --max-time 3 "$OLLAMA_ENDPOINT/api/ps"
```

`/api/tags` must list the exact configured tag
`qwen2.5-coder:1.5b`. An empty `/api/ps` is not an error: it means no model is
currently loaded. Astra checks the exact installed tag again immediately
before generation and fails closed if the provider or model disappears.

After FastAPI is running, refresh Astra's read-only capability snapshot and
inspect the durable model configuration:

```bash
curl --fail --silent --show-error \
  "http://127.0.0.1:8000/runtime/local-ai/doctor?refresh=true"
curl --fail --silent --show-error \
  "http://127.0.0.1:8000/runtime/local-ai/configuration"
curl --fail --silent --show-error \
  "http://127.0.0.1:8000/runtime/local-ai/models"
```

The configured model remains disabled until an operator explicitly enables
it with the exact `configuration_version` returned above:

```bash
curl --fail --silent --show-error \
  --request POST \
  --header 'Content-Type: application/json' \
  --data '{
    "actor_id": "local-operator",
    "enabled": true,
    "expected_configuration_version": <exact-version>,
    "idempotency_key": "enable-configured-local-model-<reviewed-identity>"
  }' \
  "http://127.0.0.1:8000/runtime/local-ai/models/configured-local-model/enabled"
```

Only after independently confirming provider/model readiness, run one
disposable advisory synthesis smoke check:

```bash
ASTRA_LOCAL_AI_GENERATION_ENABLED=1 \
ASTRA_PROJECT_SYNTHESIS_ENABLED=1 \
ASTRA_LOCAL_AI_PROVIDER=ollama \
ASTRA_OLLAMA_ENDPOINT=http://127.0.0.1:11434 \
ASTRA_LOCAL_AI_MODEL=qwen2.5-coder:1.5b \
TMP=/tmp TEMP=/tmp \
.venv/bin/python scripts/astra_phase5b_smoke.py \
  --confirm-advisory-generation
```

The confirmation flag authorizes one bounded request only. The script uses a
temporary database and workspace, explicitly enables the model only in that
temporary database, never starts or installs Ollama, never pulls a model, and
verifies that no project mutation, approval, worker request, or execution
dispatch occurred.

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
- `provider_unreachable`: the live generation readiness check could not reach the configured provider.
- `model_unavailable` / `exact_model_unavailable`: the configured exact model was not present or did not match.
- `gpu_busy`: another admitted GPU-exclusive workload owns the canonical scheduler lease; retry after it reaches a terminal state.
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
