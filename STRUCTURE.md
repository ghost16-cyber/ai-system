# Project Structure

```text
ai-coding-assistant/
|-- backend/
|   `-- app/
|       |-- main.py
|       |-- analyzer/
|       |-- core/
|       |-- database/
|       |-- jobs/
|       |-- ml/
|       |   `-- rl/
|       |-- rag/
|       |   `-- embeddings/
|       |-- llm/
|       |-- repo_scanner/
|       |-- schemas/
|       `-- tools/
|-- data/
|   |-- app/
|   |-- processed/
|   `-- models/
|-- training/
|   `-- scripts/
|-- tests/
|-- frontend/
|-- vscode-extension/
|-- docs/
|-- requirements.txt
|-- requirements-optional-experiments.txt
|-- pytest.ini
|-- README.md
`-- STRUCTURE.md
```

## Current Architecture: Deterministic Foundation

The active system is a deterministic local backend. It exposes FastAPI endpoints,
static Python rule analysis, validated single-file proposals, SQLite persistence,
and a local queued worker for project analysis.

ML, RAG, local SLM/Ollama coordination, frontend dashboard work, and VS Code
extension work remain staged future layers. They are not part of the active
runtime path.

## Active Module Mapping

| Capability | Location |
| --- | --- |
| FastAPI entry point and API orchestration | `backend/app/main.py` |
| API schemas | `backend/app/schemas/api.py` |
| AST static analyzer | `backend/app/analyzer/static_analyzer.py` |
| Rule registry and rule metadata | `backend/app/analyzer/rules/` |
| Validated deterministic fix engine | `backend/app/analyzer/fix_engine.py` |
| SQLite analysis, feedback, metrics, and patch proposal repository | `backend/app/database/repository.py` |
| SQLite job queue | `backend/app/jobs/queue.py` |
| Local worker | `backend/app/jobs/worker.py` |
| Project job handlers | `backend/app/jobs/handlers.py` |
| Repository scanner | `backend/app/repo_scanner/` |
| Tool metadata | `backend/app/tools/` |
| Utilities and configuration | `backend/app/core/` |
| Test suite | `tests/` |
| Demo flow | `docs/demo.md` |

## Inactive / Experimental Mapping

| Future Layer | Location | Current Status |
| --- | --- | --- |
| ML classification hints | `backend/app/ml/` | Preserved but inactive |
| RL experiments | `backend/app/ml/rl/` | Preserved but inactive |
| RAG and embeddings | `backend/app/rag/` | Preserved but inactive |
| Local SLM/model loading | `backend/app/llm/` | Preserved but inactive |
| Model artifacts | `data/models/` | Preserved but not loaded by active API |
| Processed training data | `data/processed/` | Preserved for later stages |
| Training scripts | `training/scripts/` | Preserved for later stages |
| Dashboard | `frontend/` | Future layer |
| VS Code extension | `vscode-extension/` | Future layer |

The existing `data/python programming/` sample-code corpus is preserved as input
data; it is not application source code.

## Runtime Boundary

The active backend has three execution paths:

1. `POST /analyze` analyzes submitted Python source text synchronously.
2. `POST /analyze-file` analyzes one Python file inside the configured workspace
   and may return compact validated patch proposals.
3. `POST /analyze-project` queues project analysis and returns a job ID. The
   local worker processes the job and stores a project-level result.

Project job results are intentionally summary-oriented. They include file paths,
parse status, findings, counts by rule/severity, and read errors. They exclude
raw source code and validated replacement text.

## Running

```bash
TMP=/tmp TEMP=/tmp python -m pytest
uvicorn backend.app.main:app --reload
python -m backend.app.jobs
```

For a complete local demonstration, see `docs/demo.md`.

## Stabilization Status

Release 4.1 exposes the FastAPI and SQLite foundation, deterministic Python AST
findings, conservative validated fixes for simple `None` and boolean
comparisons, compact single-file patch proposals, privacy-preserving aggregate
metrics, finding-linked user feedback, and queued project analysis.

Experimental ML, retrieval, and model-loading modules are preserved for staged
integration after this foundation is live-tested.
