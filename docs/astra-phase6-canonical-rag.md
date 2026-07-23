# Astra Phase 6: Canonical Project RAG

Phase 7 learned local providers and chat-native citations are documented in
`docs/astra-phase7-learned-rag.md`.

## Scope

Phase 6 adds install-free, project-bound retrieval for canonical Astra projects. It is
an advisory evidence subsystem. `ProjectRun` and `ProjectControlPlane` remain the
only lifecycle authority, while `ProjectArtifactStore` remains the immutable artifact
authority.

The implemented flow is:

```text
exact project bindings
  -> scoped repository ingestion
  -> deterministic chunks
  -> deterministic BM25 and semantic candidates
  -> versioned hybrid score
  -> bounded reranking (with lexical fallback)
  -> immutable retrieval_evidence project artifact
  -> live freshness verification
  -> optional Phase 5B synthesis evidence envelope
```

Retrieval does not approve plans, patches, commands, or manual evidence. It cannot
execute subprocesses, mutate files, transition projects, or grant authority. Every
retrieved passage is labeled `untrusted_retrieved_content`; prompt text inside a
source remains data.

## Contracts and exact bindings

Strict v1 contracts are in `backend/app/project_retrieval/contracts.py`. Retrieval is
bound to the project, actor, conversation, workspace, canonical repository root,
scope revision and hash, plan revision and hash, manifest hash, repository-state
hash, expected project state version, advisory authority identity, policy identities,
bounds, and idempotency identity.

Exact replay requires the complete durable request fingerprint. A reused idempotency
key with any different query, bound identity, policy, or bound limit is rejected.
Before a replay is returned, current project bindings, current repository bytes,
invalidation state, and the canonical artifact hash are checked again.

## Ingestion policy

Eligible repository-local extensions are:

`.py`, `.ts`, `.tsx`, `.js`, `.jsx`, `.json`, `.yaml`, `.yml`, `.toml`,
`.ini`, `.cfg`, `.md`, `.rst`, `.txt`, `.sql`, `.sh`, `.ps1`, `.html`,
`.css`, and `.xml`.

Absolute paths, `..` traversal, repository escapes, `.git`, virtual environments,
`node_modules`, caches, build output, archives, databases, model directories,
`.env` files, private keys, and common secret files are excluded. Canonical scope
inclusion and exclusion paths are applied after the path has been normalized.

Source identity uses exact SHA-256 bytes. Modification time is not authority.
Unchanged sources and chunks are reused. Changed, deleted, or newly excluded sources
are made inactive while their historical rows remain available for audit.

## Chunking and retrieval

Chunking policy `astra.rag.chunking.v1` uses stable UTF-8 line windows, Markdown
heading context, a 4,000-character target, a 6,000-character maximum, and an
eight-line overlap. A source is bounded to 750,000 bytes and 256 chunks. Ingestion is
also bounded by file count, aggregate bytes, and project chunk count.

BM25 tokenization uses case-folded ASCII alphanumeric/underscore terms. Semantic
search uses a bounded provider interface; the install-free implementation is a
deterministic hash embedding and an explicit unavailable provider is supported.
Hybrid v1 combines normalized BM25 (55 percent) and cosine semantic score
(45 percent), with stable path/ordinal/chunk tie-breaks.

Reranking receives no more than the validated request bound and only bounded
candidate text. Invalid or unavailable reranking falls back to deterministic lexical
scoring. Unknown IDs, duplicates, and non-finite scores fail validation.

## Persistence and invalidation

Migration 16 adds normalized corpus, source, chunk, embedding, request, candidate,
evidence, replay, and invalidation tables. Final evidence is also written as the
canonical `retrieval_evidence` project artifact. Retrieval artifact rows have
database update/delete protection and are reconstructible after restart.

Relevant binding changes invalidate replay use. In addition, every artifact is
checked against current repository bytes immediately before it can be attached to
Phase 5B. Stale or invalidated evidence cannot cross that boundary.

## Phase 5B boundary

`ProjectRetrievalService.phase5b_evidence` accepts an artifact ID plus the exact
retrieval request binding. The backend loads the stored artifact, verifies its
canonical hash and live bindings, and produces the bounded
`RetrievalPhase5BEvidence` contract. Clients cannot use that method to supply raw
passage text.

`SynthesisEvidenceEnvelope` accepts this typed optional attachment and enforces exact
project, scope, plan, manifest, and repository-state equality. Deterministic
synthesis-first behavior is unchanged; retrieval does not force a model call.
Provider instructions explicitly classify retrieved passages as quoted, advisory,
untrusted reference material.

## API

- `POST /chat/projects/{project_id}/rag/ingest`
- `POST /chat/projects/{project_id}/rag/retrieve`
- `GET /chat/projects/{project_id}/rag/status`
- `GET /chat/projects/{project_id}/rag/artifacts`
- `GET /chat/projects/{project_id}/rag/artifacts/{artifact_id}`

The routes contain no lifecycle, approval, mutation, or execution operation.
No frontend lifecycle was added: the canonical artifact listing already hydrates
the new artifact kind, and the typed status/artifact APIs are reload-safe. A future
evidence presentation card can consume these reads without becoming an authority.

## Local validation

```bash
.venv/bin/python -m backend.app.project_retrieval.smoke
```

The smoke check creates a temporary repository and canonical project, ingests,
retrieves, verifies exact replay, changes a source, confirms that stale evidence is
rejected by the Phase 5B boundary, and removes all temporary resources.

Focused tests:

```bash
.venv/bin/python -m pytest -q \
  tests/test_rag_contracts.py \
  tests/test_rag_chunking.py \
  tests/test_rag_providers.py \
  tests/test_rag_integration.py \
  tests/test_rag_api.py \
  tests/test_rag_smoke.py
```

## Known limitations

- The initial semantic provider is intentionally deterministic and local; no
  embedding model is downloaded.
- Vector lookup is bounded brute-force SQLite reconstruction, appropriate for the
  Phase 6 corpus limits rather than a large external vector service.
- Only repository-local UTF-8 sources are ingested by the public endpoint. External
  URLs and arbitrary host paths are not supported.
- The backend exposes canonical APIs and hydration-compatible artifacts; a richer
  citation UI is deferred to avoid introducing a second frontend lifecycle model.
