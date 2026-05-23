# AI Coding Assistant

An AI-assisted code analysis project combining static repository inspection, a
pattern classifier, retrieval-augmented context, and optional LLM reasoning.

## Layout

Application code now lives under `backend/app/`:

- `analyzer/`: inference pipeline and code/file analysis.
- `core/`: caching, logging, memory monitoring, and configuration.
- `llm/`: model loading and quantization.
- `ml/`: classifiers and reinforcement-learning helpers.
- `rag/`: retrieval, prompt construction, and embeddings.
- `repo_scanner/`: repository scanning, planning, inspection, and reasoning.
- `api/`, `database/`, `schemas/`: reserved for the FastAPI and SQLite work.

Models and prepared datasets remain in `data/models/` and `data/processed/`.
Training commands remain under `training/scripts/`.

## Commands

```bash
python -m pytest
python training/scripts/train_classifier.py
python training/scripts/build_rag.py
python -m backend.app.main
python -m backend.app.repo_scanner.cli . --limit 20
```

The application entry point expects trained artifacts in `data/models/`.
Detailed directory information is in `STRUCTURE.md`.
