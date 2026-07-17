# Astra — Local-first Coding Assistant

A local-first Python coding assistant backend built in working releases.

## Current Checkpoint: Stage 0 — Trustworthy Intake and Approval Integrity

The active system is a local FastAPI backend plus a single chat-native React
interface. Stage 0 hardens assignment intake, dataset grounding, project-plan
approval, repository staleness, and acceptance verification. It is a security
and integrity checkpoint, not a claim of full production readiness.

### Active Capabilities

- FastAPI service with direct code/file analysis, queued project analysis, rule/tool discovery, feedback, history, metrics, and job status endpoints.
- SQLite persistence for analysis metadata, finding metadata, feedback, metrics, patch proposals, and queued jobs.
- Privacy-preserving history: raw submitted code is not stored; history retains a SHA-256 hash and request metadata.
- Python-only API boundary.
- Python `ast` static analysis with structured rule findings and source locations.
- Conservative deterministic fix suggestions with validation.
- Compact patch proposals for validated single-file fixes returned by `POST /analyze-file`; no file edits are applied automatically.
- Local SQLite-backed worker for queued project analysis.
- SLM-ready orchestrator job loop with structured task state, advisor hooks, safe tool routing, policy-gated edits, validators, and redacted traces.
- Project analysis summaries that exclude source code and validated replacement text.
- Finding-linked feedback for helpfulness and validated suggestion acceptance.
- Hardware-aware AI optimizer report endpoint with low-VRAM training recommendations.
- Local Runtime Intelligence context for hardware, installed tools, capability planning, and task-specific execution settings.
- Runtime-aware plan gating with trace audits and benchmark decision metrics.
- Chat-native assignment parsing, planning, evidence, report, and workspace flows.
- Stage 9 `project_delivery` as the canonical delivery aggregate, bridged to the
  legacy project-job execution machinery.
- Immutable approved plan revisions with separately persisted work-unit runtime state.
- Complete, fail-closed project-state manifests and fresh typed verifier results.
- Explicit plan, patch, command, scope, rollback, and human-validation approval boundaries.

### Explicit boundaries and later stages

Some experimental modules remain in the repository, but Stage 0 does not make
them trusted execution authorities. File changes and commands remain approval
gated. Command sandboxing and filesystem/network isolation are a later Stage 2
requirement; Stage 0 does not provide container isolation. Distributed workers,
cloud model adapters, GPU scheduling, team collaboration, and a completed VS
Code extension also remain outside this checkpoint.

See [`docs/stage0-trust-integrity.md`](docs/stage0-trust-integrity.md) for the
contracts, failure behavior, compatibility policy, and regression commands.

## Active Rules

| Rule | Category |
| --- | --- |
| `syntax_error` | Correctness |
| `bare_except` | Reliability |
| `dangerous_eval` | Security |
| `dangerous_exec` | Security |
| `mutable_default_argument` | Correctness |
| `bad_none_comparison` | Style |
| `redundant_boolean_comparison` | Style |
| `missing_docstring` | Maintainability |
| `unused_import` | Maintainability |
| `inefficient_loop` | Performance |

## Active Validated Fixes

The deterministic fix engine currently generates replacements for simple `None`
comparisons and simple boolean comparisons:

| Finding | Replacement |
| --- | --- |
| `value == None` | `value is None` |
| `value != None` | `value is not None` |
| `flag == True` | `flag` |
| `True == flag` | `flag` |
| `flag != False` | `flag` |
| `False != flag` | `flag` |
| `flag == False` | `not flag` |
| `False == flag` | `not flag` |
| `flag != True` | `not flag` |
| `True != flag` | `not flag` |

Each proposed replacement must:

- parse as valid Python;
- remove exactly one target finding;
- introduce no new medium/high severity finding.

For file analysis, a validated single-line replacement is also returned as a
patch proposal with the file hash and line number. Proposals are not applied
automatically.

Other findings keep guidance and report `validation.status: "not_available"`.

## Active Metrics

`GET /metrics` returns:

- total, clean, and finding-producing analyses;
- total and average findings;
- parse failure and validated-fix totals;
- fixable finding and validated-fix coverage totals;
- finding counts grouped by rule and severity;
- validation status counts;
- feedback totals and suggestion acceptance rate.

The database stores finding metadata needed for these totals, such as rule ID,
severity, and validation status. It does not store submitted code or suggested
replacement code.

## Active Feedback

Each issue returned by `/analyze` includes a `finding_id`. Submit feedback using
that ID and its `analysis_id`:

```json
{
  "analysis_id": "...",
  "finding_id": "...",
  "helpful": true,
  "suggestion_accepted": true
}
```

`suggestion_accepted` is optional and is accepted only for findings with a
validated replacement. Guidance-only findings can still receive helpfulness
feedback. Re-submitting feedback for a finding updates its current judgment
rather than inflating metrics.

Metrics also report feedback totals, helpful/unhelpful counts, accepted or
rejected validated suggestions, and suggestion acceptance rate.

## Run It

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
TMP=/tmp TEMP=/tmp python -m pytest
uvicorn backend.app.main:app --reload
```

Run the single local worker in a second terminal to process queued project
analysis:

```bash
python -m backend.app.jobs
```

Generate and run the controlled repair benchmark:

```bash
python tools/generate_repair_benchmark_cases.py
python tools/run_repair_benchmark.py --allow-edits
```

For SLM-backed benchmark runs, keep Ollama, the FastAPI server, and the local
worker running. The benchmark copies cases into `benchmarks/.runs/`, queues
`POST /orchestrate` jobs, polls job status, and writes a JSON report.

The live API is then available at:

- `GET /health`
- `POST /analyze`
- `POST /analyze-file`
- `POST /analyze-project`
- `POST /orchestrate`
- `GET /hardware-ai/report`
- `GET /runtime/context`
- `POST /runtime/validate-plan`
- `POST /runtime/execution-profile`
- `GET /rules`
- `GET /tools`
- `POST /feedback`
- `GET /history`
- `GET /metrics`
- `GET /jobs`
- `GET /jobs/{job_id}`
- `POST /jobs/{job_id}/cancel`
- Interactive API documentation at `/docs`

See `docs/demo.md` for a complete local demonstration flow.

Set `AI_SYSTEM_DB_PATH` to use a different SQLite path. The default local
database is `data/app/ai_system.db`, which is excluded from Git. Set
`AI_SYSTEM_WORKSPACE_ROOT` to limit `POST /analyze-file` to a specific local
project root; file and project requests must stay within that root.

The `TMP=/tmp TEMP=/tmp` prefix prevents WSL pytest capture errors when the
shell inherits a Windows temporary directory.

## Next Step

Continue validating Stage 0 on real local projects before increasing autonomy.
Stage 2 command isolation remains a separate prerequisite for stronger execution
authority; model or classifier output must remain non-authoritative for approval
and verification decisions.
