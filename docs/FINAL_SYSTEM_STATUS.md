# Historical System Status: Deterministic Foundation Stabilized

> Historical record only. The active canonical local-MVP status and operator
> procedures are documented in [`../README.md`](../README.md) and
> [`astra-local-operations.md`](astra-local-operations.md).

## Checkpoint

The active system is a local-first Python coding assistant backend at the
foundation stabilization checkpoint. It combines deterministic analysis,
validated fix proposals, metadata and feedback persistence, and a lightweight
SQLite-backed project-analysis worker.

This document describes the active runtime. Older experimental ML, RAG, and
model-loading files may remain in the repository, but they are not active
application capabilities.

## Active Runtime

### Deterministic Analysis

The FastAPI backend analyzes Python source using `ast` and the custom rule
registry. It currently reports these deterministic issue types:

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

### Validated Proposals

The fix engine creates deterministic proposals for simple `None` and boolean
comparisons. Before a proposal is exposed as validated, the system checks that:

- the suggested Python source parses;
- exactly one target finding is removed;
- no new medium- or high-severity finding is introduced.

For `POST /analyze-file`, validated single-line replacements may also be stored
as compact patch proposals linked to a file hash and source line. The backend
does not automatically apply those proposals.

### Local Job Worker

Project analysis uses the SQLite-backed job queue under `backend/app/jobs/`.
`POST /analyze-project` queues an allowlisted job; a separate local worker
executes it:

```bash
python -m backend.app.jobs
```

The project job reuses repository scanning for file discovery and uses the
production deterministic analyzer for Python findings. Results contain paths,
counts, parse status, findings, and read errors; they do not contain raw
source code or replacement code.

### Persistence and Privacy

SQLite stores:

- analysis metadata and SHA-256 source hashes;
- finding metadata;
- feedback and suggestion-acceptance judgments;
- aggregate metrics;
- compact validated patch proposals;
- queued job status and structured job results.

Raw submitted or scanned source is not stored by default.

## Active API

| Endpoint | Purpose |
| --- | --- |
| `GET /health` | Backend and database health |
| `POST /analyze` | Synchronous Python snippet analysis |
| `POST /analyze-file` | Workspace-scoped Python file analysis and validated proposals |
| `POST /analyze-project` | Queue workspace-scoped project analysis |
| `GET /rules` | Active deterministic rule metadata |
| `GET /tools` | Current coordinator-facing tool metadata |
| `POST /feedback` | Record finding feedback |
| `GET /history` | Analysis metadata history |
| `GET /metrics` | Aggregate findings, validation, and feedback metrics |
| `GET /jobs` | Recent job status |
| `GET /jobs/{job_id}` | Job result or status |
| `POST /jobs/{job_id}/cancel` | Request job cancellation |

## Inactive Layers

The following are intentionally not enabled in the active backend:

- ML classifier hints or statistical ranking;
- RAG retrieval and embeddings;
- local SLM/Ollama explanations or tool coordination;
- autonomous code changes or patch application;
- web dashboard;
- VS Code extension.

These remain later roadmap layers after the deterministic tool and job
foundation has been exercised.

## Verification

The current backend verification suite passes:

```text
60 passed
```

The next planned intelligence layer is non-authoritative ML hints, returned
separately from deterministic findings and never used to authorize code
changes.
