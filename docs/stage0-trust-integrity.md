# Astra Stage 0: Trustworthy Intake and Approval Integrity

Stage 0 closes trust-boundary defects in document intake, dataset grounding,
project approval, repository identity, and completion verification. Stage 9
`project_delivery` remains the canonical delivery aggregate. Existing
project-job records continue to provide the bounded implementation, command,
repair, and rollback bridge; Stage 0 does not introduce a second workflow.

## Trusted contracts

Assignment documents are represented as versioned ordered `DocumentBlock`
records. Paragraphs, headings, lists, tables, rows, and cells retain raw text,
normalized text, deterministic IDs, source spans, style metadata, and their
original body order. Assignment sections, tasks, criteria, and evidence retain
block IDs and source spans. DOCX limits fail with typed errors instead of
silently dropping content.

`DatasetSemanticMapping` records real source fields and deterministic
`DerivedColumnPlan` operations. Separate Date and Time fields can produce an
`event_timestamp`, then hour and weekday dimensions. A numeric zero-boundary
band is available when it is the only safe grouping dimension. Every derived
field names its sources and operation. Profiled but unresolved mappings fail
before code/dashboard generation; they do not emit runnable placeholder code.

An `ExecutionPlanRevision` contains only approved immutable definitions and a
canonical content hash. `WorkUnitExecutionState` stores active, completed,
failed, blocked, cancelled, and rolled-back state separately. An approval grant
binds the project run, exact revision ID and content hash, task/scope revision,
workspace/root identity, actor, timestamp, and expected state version. Runtime
status never contributes to the plan content hash.

`ProjectStateManifest` hashes every relevant file inside explicit scanner
bounds, in normalized sorted path order, using streaming SHA-256 reads. It
records exclusions and scanner policy. Cache directories, secrets, symlinks,
binary artifacts, and large assignment/dataset inputs are excluded by explicit
policy. Source/config/general project files remain fail-closed. A file-count,
depth, total-byte, unsafe-path, or relevant-file-size limit makes the manifest
incomplete, and an incomplete manifest cannot authorize approval or verification.

`VerifierResult` is immutable typed evidence from a separate deterministic
verifier invocation. It binds the current plan revision, criterion definition,
work unit, complete manifest, checker version, performed checks, observations,
evidence hashes, outcome, and result hash. File presence uses the manifest;
Python structure uses AST; JavaScript/TypeScript uses bounded structural
matching; configuration uses typed parsing and selectors; exact assertions use
bounded content or patch scope; command criteria require a real approved command
result. Manual criteria return `manual_required`, never an automatic pass.

## Failure and freshness behavior

- Document limit or unsupported-structure errors reject intake without truncation.
- Missing dataset semantics return typed unresolved requirements; profiled code
  generation requires a resolved mapping.
- Repository changes between planning and approval require a fresh plan.
- Changes to objectives, files, dependencies, criteria, inputs, outputs, or
  execution boundaries require a new plan revision and approval.
- Manifest changes, criterion changes, plan supersession, or result-hash changes
  make verifier evidence stale.
- Handoff completion uses only fresh, passed, typed verifier evidence and rechecks
  the live manifest through the API.
- The chat card renders unsupported legacy satisfaction claims as stale rather
  than green/successful.

Stage 0 audit records are bounded and exclude raw file content and secrets. They
cover document parse results, manifest and plan-revision creation/rejection,
approval grants/rejections, verifier start/completion, and stale-evidence rejection.

## Persistence and compatibility

SQLite initialization is repeatable and enables foreign-key enforcement. New
normalized tables store immutable plan revisions, work-unit runtime states,
project manifests, verifier results, and bounded Stage 0 audit events while the
aggregate JSON remains available for API/reload compatibility. Updates remain
transactional and optimistic-state-version guarded.

Legacy mutable plans are adapted into a v2 immutable projection. A legacy
approval is discarded and recorded as `migration_reapproval_required`; it never
authorizes the new revision. Legacy API plan status remains a derived projection
only. Existing chat and project-job routes continue to work as Stage 9 adapters.

## Regression and validation commands

From the repository root with the virtual environment installed:

```bash
.venv/bin/python -m pytest -q tests/test_stage0_trust_integrity.py
.venv/bin/python -m pytest -q tests/test_assignments_parser.py tests/test_assignments_extractor_planner.py tests/test_assignments_dataset_mapper.py tests/test_assignments_code_blueprints.py tests/test_project_delivery.py tests/test_project_diagnosis.py
.venv/bin/python -m pytest
```

Frontend validation:

```bash
cd frontend
node --experimental-strip-types --test tests/*.test.ts
npx tsc -b --pretty false
npx eslint .
npm run build
```

On this Windows/WSL workspace, use the configured Node runtime or a mapped WSL
path if the Windows Node executable cannot resolve a UNC working directory.

## Security boundary

Stage 0 improves integrity and fail-closed behavior; it does not sandbox command
execution or isolate the process from the filesystem/network. That is a later
Stage 2 requirement. Continue using the existing exact plan, patch, command,
scope, rollback, and human-validation approvals, and do not treat model output
as evidence.
