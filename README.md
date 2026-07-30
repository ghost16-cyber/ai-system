# AI Coding Assistant

A local-first Python coding assistant backend built in working releases.

## Current Checkpoint: Release 4.1 - Foundation Stabilization

Release 4.1 stabilizes the deterministic backend before any ML, RAG, or SLM
layer is activated. The current system is a local FastAPI service with
allowlisted tools, deterministic Python analysis, validated proposals, feedback,
metrics, and queued project analysis.

### Active Capabilities

- FastAPI service with direct code/file analysis, queued project analysis, rule/tool discovery, feedback, history, metrics, and job status endpoints.
- SQLite persistence for analysis metadata, finding metadata, feedback, metrics, patch proposals, and queued jobs.
- Privacy-preserving history: raw submitted code is not stored; history retains a SHA-256 hash and request metadata.
- Python-only API boundary.
- Python `ast` static analysis with structured rule findings and source locations.
- Conservative deterministic fix suggestions with validation.
- Compact patch proposals for validated single-file fixes returned by `POST /analyze-file`; no file edits are applied automatically.
- Local SQLite-backed worker for queued project analysis.
- Project analysis summaries that exclude source code and validated replacement text.
- Finding-linked feedback for helpfulness and validated suggestion acceptance.

### Inactive / Future Layers

The repository still contains experimental modules for later stages, but they are
not loaded by the active backend:

- ML classifier hints are inactive.
- RAG and embeddings are inactive.
- Local SLM/Ollama coordination is inactive.
- Dashboard and VS Code extension layers are inactive.
- No automatic code rewriting is active.

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

The live API is then available at:

- `GET /health`
- `POST /analyze`
- `POST /analyze-file`
- `POST /analyze-project`
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

After this stabilization checkpoint, the next planned implementation layer is a
non-authoritative ML hints layer. ML hints should be returned separately from
rule findings, should not produce validated patches, and should not affect safety
decisions.
