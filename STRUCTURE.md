# Project Structure

```text
ai-coding-assistant/
|-- backend/
|   `-- app/
|       |-- main.py
|       |-- api/
|       |-- core/
|       |-- analyzer/
|       |-- ml/
|       |   `-- rl/
|       |-- rag/
|       |   `-- embeddings/
|       |-- llm/
|       |-- repo_scanner/
|       |-- database/
|       `-- schemas/
|-- data/
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

## Module Mapping

| Capability | Location |
| --- | --- |
| Active FastAPI entry point | `backend/app/main.py` |
| Active SQLite history, feedback and metrics repository | `backend/app/database/repository.py` |
| Active API schemas | `backend/app/schemas/api.py` |
| Active AST static analyzer | `backend/app/analyzer/static_analyzer.py` |
| Active validated fix engine | `backend/app/analyzer/fix_engine.py` |
| Experimental code analysis | `backend/app/analyzer/` |
| Experimental ML classification | `backend/app/ml/` |
| Experimental retrieval and embeddings | `backend/app/rag/` |
| Experimental LLM model loading | `backend/app/llm/` |
| Repository scanner/planner | `backend/app/repo_scanner/` |
| Utilities and configuration | `backend/app/core/` |
| Model artifacts | `data/models/` |
| Processed training data | `data/processed/` |
| Training scripts | `training/scripts/` |

The existing `data/python programming/` sample-code corpus is preserved as
input data; it is not application source code.

## Running

```bash
TMP=/tmp TEMP=/tmp python -m pytest
uvicorn backend.app.main:app --reload
python -m backend.app.repo_scanner.cli .
```

Release 4 exposes the FastAPI and SQLite foundation, deterministic Python AST
findings, conservative validated fixes for simple `None` comparisons,
privacy-preserving aggregate metrics, and finding-linked user feedback.
Experimental ML, retrieval, and model-loading modules are preserved for staged
integration after this backend layer is live-tested.
