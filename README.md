# AI Coding Assistant

A local-first Python learning assistant built in working releases.

## Current Release: 4 - Feedback Capture

Release 4 adds user judgments to the deterministic analyzer:

- FastAPI service with `GET /health`, `POST /analyze`, `GET /rules`, `GET /tools`, `POST /feedback`, `GET /history`, and `GET /metrics`.
- SQLite persistence for analysis metadata.
- Raw submitted code is not stored; history retains a SHA-256 hash and request metadata.
- Python-only API boundary.
- Python `ast` static analysis with structured rule findings and source locations.
- Conservative deterministic fix suggestions with validation.
- SQLite-backed aggregate metrics for analyses, findings, parsing, and validated fixes.
- Finding-linked feedback for helpfulness and validated suggestion acceptance.
- No ML, RAG, local model, or automatic code rewriting is active yet.

Existing experimental analyzer, model, and retrieval modules remain in the
repository for evaluation in later releases. They are not loaded by the active
static analysis service.

### Active Rules

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

### Active Validated Fixes

Only simple `None` comparisons currently receive a generated replacement:

- `value == None` -> `value is None`
- `value != None` -> `value is not None`

Each proposed replacement must:

- parse as valid Python;
- remove exactly one target finding;
- introduce no new medium/high severity finding.

Other findings keep guidance and report `validation.status: "not_available"`.

### Active Metrics

`GET /metrics` returns:

- total, clean, and finding-producing analyses;
- total and average findings;
- parse failure and validated-fix totals;
- finding counts grouped by rule and severity;
- validation status counts.

The database stores finding metadata needed for these totals, such as rule ID,
severity, and validation status. It does not store submitted code or suggested
replacement code.

### Active Feedback

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

Metrics now also report feedback totals, helpful/unhelpful counts, accepted or
rejected validated suggestions, and suggestion acceptance rate.

## Run It

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
TMP=/tmp TEMP=/tmp python -m pytest
uvicorn backend.app.main:app --reload
```

The live API is then available at:

- `GET /health`
- `POST /analyze`
- `GET /rules`
- `GET /tools`
- `POST /feedback`
- `GET /history`
- `GET /metrics`
- Interactive API documentation at `/docs`

Example request:

```bash
curl -X POST http://127.0.0.1:8000/analyze \
  -H 'Content-Type: application/json' \
  -d '{"code":"print(\"hello\")\n","language":"python","filename":"demo.py"}'
```

Set `AI_SYSTEM_DB_PATH` to use a different SQLite path. The default local
database is `data/app/ai_system.db`, which is excluded from Git.

The `TMP=/tmp TEMP=/tmp` prefix prevents WSL pytest capture errors when the
shell inherits a Windows temporary directory.

## Next Step

Before adding more issue types or broader fixes, this layer should be exercised
with realistic code samples. The next implementation layer can add another
carefully validated fix template or caching for repeated analyses.
