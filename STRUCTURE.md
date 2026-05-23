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
|-- pytest.ini
|-- README.md
`-- STRUCTURE.md
```

## Module Mapping

| Capability | Location |
| --- | --- |
| Main pipeline entry point | `backend/app/main.py` |
| Inference and code analysis | `backend/app/analyzer/` |
| Fast ML classification | `backend/app/ml/` |
| Retrieval and embeddings | `backend/app/rag/` |
| LLM model loading | `backend/app/llm/` |
| Repository scanner/planner | `backend/app/repo_scanner/` |
| Utilities and configuration | `backend/app/core/` |
| Model artifacts | `data/models/` |
| Processed training data | `data/processed/` |
| Training scripts | `training/scripts/` |

The existing `data/python programming/` sample-code corpus is preserved as
input data; it is not application source code.

## Running

```bash
python -m pytest
python training/scripts/train_classifier.py
python training/scripts/build_rag.py
python -m backend.app.main
python -m backend.app.repo_scanner.cli .
```

`backend/app/api/`, `backend/app/database/`, and `backend/app/schemas/` are
ready for the planned FastAPI endpoint and SQLite analysis storage.
