# Stage 2A — Durable Typed Worker Queue

## Purpose

Stage 2A introduces a durable local worker queue without creating a second project lifecycle authority. The canonical `ProjectRun` aggregate and `ProjectControlPlane.execute()` remain the only owners of lifecycle state.

The queue owns only worker-delivery state: enqueueing, leasing, heartbeat renewal, cancellation requests, bounded terminal results, lease expiry, and reconciliation bookkeeping.

## Ownership boundary

```text
ProjectControlPlane.execute()
    creates an immutable canonical ExecutionAttempt
                │
                ▼
ProjectWorkerQueue
    validates exact current bindings and stores one worker request
                │
                ▼
Local worker runtime
    claims a time-bounded lease and performs bounded work
                │
                ▼
ProjectWorkerService
    records queue outcome and reconciles non-success attempts
                │
                ▼
ProjectControlPlane.execute(RECOVER_ATTEMPT)
    remains the sole lifecycle writer
```

Successful worker results remain pending until the Stage 2B execution adapter submits the exact domain result command, such as `RECORD_PATCH_RESULT`, `RECORD_COMMAND_RESULT`, or `RECORD_VERIFIER_RESULT`.

## Durable records

Stage 2A adds:

- `project_worker_requests`: one durable request per canonical execution attempt.
- `project_worker_events`: append-only queue and lease events.
- `project_worker_idempotency`: deterministic enqueue and completion replay.

Each request is bound to the exact project, execution attempt, actor, conversation, workspace, repository root and fingerprint, plan revision, scope revision, manifest hash, authority scope, and expected project state version.

## Lease behavior

- Claiming uses `BEGIN IMMEDIATE` and a compare-and-set update.
- Only one worker can obtain a request.
- Raw lease tokens are returned once and only their SHA-256 hashes are stored.
- Heartbeats require the exact worker identity and lease token.
- Expired leases become `interrupted`; they are never automatically requeued.
- A cancellation-requested expired lease becomes `cancelled`.

## Crash consistency

Terminal non-success worker requests carry a durable `canonical_reconciled_at` marker.

If the process stops after the queue result is committed but before the canonical attempt is recovered, the next recovery pass selects all unreconciled terminal failures and retries the control-plane reconciliation. Reconciliation is idempotent and does not re-execute work.

## Current limits

Stage 2A does not execute operating-system processes. Process spawning, workspace isolation, command allowlists, resource enforcement, output capture, and exact successful-result adapters are Stage 2B.

The queue remains local, single-node, and SQLite-backed.
