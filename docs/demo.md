# Foundation Demo Flow

This demo shows the active deterministic backend only. It does not use ML, RAG,
Ollama, an SLM, a dashboard, or a VS Code extension.

## 1. Prepare the environment

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
TMP=/tmp TEMP=/tmp python -m pytest
```

## 2. Start the API

Terminal 1:

```bash
uvicorn backend.app.main:app --reload
```

The API documentation is available at:

```text
http://127.0.0.1:8000/docs
```

## 3. Start the local worker

Terminal 2:

```bash
python -m backend.app.jobs
```

The worker processes queued project-analysis jobs from the local SQLite queue.

## 4. Check service health

Terminal 3:

```bash
curl http://127.0.0.1:8000/health
```

Expected result: `status` should be `ok`, and the database status should be
`ready`.

## 5. Analyze a code snippet

```bash
curl -X POST http://127.0.0.1:8000/analyze \
  -H 'Content-Type: application/json' \
  -d '{"code":"if flag == True:\n    print(flag)\n","language":"python","filename":"demo.py"}'
```

Expected result:

- one `redundant_boolean_comparison` finding;
- a validated replacement;
- no patch proposal, because `/analyze` does not operate on a workspace file.

## 6. Analyze a file and inspect a patch proposal

Create a demo file:

```bash
mkdir -p demo_workspace
cat > demo_workspace/sample.py <<'PY'
if value == None:
    print(value)
PY
```

Start the API with a workspace root:

```bash
AI_SYSTEM_WORKSPACE_ROOT="$PWD/demo_workspace" uvicorn backend.app.main:app --reload
```

Then call:

```bash
curl -X POST http://127.0.0.1:8000/analyze-file \
  -H 'Content-Type: application/json' \
  -d '{"path":"sample.py"}'
```

Expected result:

- one `bad_none_comparison` finding;
- a validated replacement;
- one compact patch proposal with file hash, line number, and replacement line;
- no automatic file modification.

## 7. Queue project analysis

```bash
curl -X POST http://127.0.0.1:8000/analyze-project \
  -H 'Content-Type: application/json' \
  -d '{"path":"."}'
```

Expected result:

```json
{
  "job_id": "...",
  "status": "queued",
  "status_url": "/jobs/..."
}
```

## 8. Retrieve job results

Use the returned `status_url`:

```bash
curl http://127.0.0.1:8000/jobs/<job_id>
```

Expected result after the worker processes the job:

- `status` should be `succeeded`;
- `result.python_files_analyzed` should show how many Python files were scanned;
- `result.total_findings` should show the project finding count;
- `result.findings_by_rule` and `result.findings_by_severity` should summarize findings;
- project results should not include raw source code or validated replacement text.

## 9. Check aggregate metrics

```bash
curl http://127.0.0.1:8000/metrics
```

Expected result: aggregate counts for analyses, findings, parse failures,
validated fixes, validation statuses, and feedback.

## Demo boundary

The active demo proves the deterministic foundation:

- structured Python AST findings;
- conservative validated proposals;
- safe file-scoped patch proposals;
- queued project analysis;
- local SQLite persistence;
- privacy-preserving history and project results.

ML hints, RAG context, SLM coordination, dashboard visualizations, and VS Code
extension integration are future layers and are intentionally absent from this
demo.
