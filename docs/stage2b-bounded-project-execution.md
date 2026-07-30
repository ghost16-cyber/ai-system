# Stage 2B — Bounded Project Execution

## Purpose

Stage 2B turns the Stage 2A durable worker queue into a controlled local subprocess runtime. It does not create a second project lifecycle. The canonical `ProjectControlPlane` remains the only authority that can complete or interrupt an `ExecutionAttempt`.

## Ownership

| Concern | Owner |
|---|---|
| Project lifecycle and canonical attempt state | `ProjectControlPlane.execute()` |
| Durable request, lease, heartbeat and terminal worker state | `ProjectWorkerQueue` |
| Terminal worker-to-canonical reconciliation | `ProjectWorkerService` |
| Command policy, path containment and artifact integrity | `project_workers.policy` |
| Process start, timeout, cancellation, output and evidence | `ProjectSubprocessExecutor` |

## Supported attempt types

The bounded subprocess executor claims only:

- `command_execution`
- `verification`

Patch application, rollback, repair synthesis, work-unit planning and handoff generation are not silently treated as shell commands.

## Supported actions

- `pytest`
- `python_script`
- `npm_test`
- `npm_run_lint`
- `npm_run_build`
- `npm_run_typecheck`
- `node_test`
- `docker_ps`

Every action is converted to an argument vector and executed with `shell=False`. Arbitrary shell command strings are not accepted.

## Exact authority binding

A worker execution specification is content-addressed. Its `execution_hash` must match the immutable authority stored on the canonical `ExecutionAttempt`. Command executions also require the exact approved `command_id`; verifier executions require the exact `criterion_id` and `criterion_hash`.

The queue independently binds the request to the current actor, conversation, workspace, repository identity, plan revision, scope revision, manifest and project state version.

## Workspace containment

Before process creation, the runtime:

1. Revalidates the approved repository root fingerprint.
2. Requires a relative working directory inside that root.
3. Rejects symlink path components.
4. Requires executable input files to be regular files inside the root.
5. Recomputes every approved input artifact SHA-256 hash.
6. Rejects changed, missing or unapproved targets.

## Process boundaries

The runtime provides:

- no shell invocation;
- no stdin;
- a minimal environment without inherited proxy settings;
- isolated temporary HOME, cache and Python bytecode locations;
- process-group termination on timeout or cancellation;
- optional POSIX address-space and CPU-time limits;
- bounded stdout and stderr capture;
- secret-pattern redaction;
- atomic, content-hashed evidence records;
- lease heartbeat renewal while the process is active.

## Cancellation and timeout

Running workers poll the durable queue state. A `cancel_requested` request terminates the process group and is acknowledged as `cancelled`. A deadline breach terminates the process group and records `timed_out`.

Both outcomes are reconciled through canonical `recover_attempt`; they are never automatically requeued or re-executed.

## Crash consistency

A worker result is first persisted in the worker queue. Canonical reconciliation is tracked separately by `canonical_reconciled_at`.

Startup recovery scans every unreconciled terminal worker request, including successful requests. This covers a process crash after the queue committed a result but before the control-plane command committed. Reconciliation is idempotent and still checks current plan, scope and manifest bindings.

## Successful completion

A successful command request is converted to `record_command_result`. A successful verifier request is converted to `record_verifier_result`. The worker cannot directly update canonical attempt JSON or lifecycle state.

## Explicit limitation

Stage 2B is a controlled local subprocess runtime, not an operating-system or container sandbox. It does not enforce network isolation and cannot prevent approved project code from attempting filesystem access allowed to the Astra process account. Strong filesystem and network isolation remain Stage 2C work.
