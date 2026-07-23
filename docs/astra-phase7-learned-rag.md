# Astra Phase 7: Learned Local Retrieval and Chat-Native Evidence

## Decision

Phase 7 retains the Phase 6 canonical advisory RAG architecture and replaces only
the provider implementations used by the production application.

- Primary embedding: `BAAI/bge-small-en-v1.5`
- Explicit embedding fallback: `sentence-transformers/all-MiniLM-L6-v2`
- Learned reranker: `cross-encoder/ms-marco-MiniLM-L6-v2`
- Storage: bounded SQLite vectors and brute-force cosine comparison
- Network policy: local Hugging Face snapshots only

The deterministic embedding and lexical reranking providers remain available for
tests, offline fallback, evaluation, and install-free smoke checks.

## Phase 7A evidence

The local benchmark supplied for the project used Python 3.12.3, PyTorch
2.12.0+cu130, Transformers 5.9.0, sentence-transformers 5.5.1, an RTX 3050 Laptop
GPU with 4 GiB VRAM, and 11.69 GiB WSL RAM.

| Provider | Device | Median latency | Recall@1 | Recall@3 | MRR | Peak VRAM |
|---|---:|---:|---:|---:|---:|---:|
| all-MiniLM-L6-v2 query | CPU | 7.826 ms | 0.75 | 1.00 | 0.875 | n/a |
| all-MiniLM-L6-v2 query | CUDA | 9.698 ms | 0.75 | 1.00 | 0.875 | 99.20 MiB |
| bge-small-en-v1.5 query | CPU | 10.163 ms | 0.75 | 1.00 | 0.875 | n/a |
| bge-small-en-v1.5 query | CUDA | 12.592 ms | 0.75 | 1.00 | 0.875 | 227.72 MiB |
| cross-encoder, 24 pairs | CPU | 117.567 ms | 1.00 | 1.00 | 1.00 | n/a |
| cross-encoder, 24 pairs | CUDA | 24.561 ms | 1.00 | 1.00 | 1.00 | 229.40 MiB |

These results justify CPU for a single embedding query and admitted CUDA for
larger ingestion/reranking batches. They are small-fixture measurements, not
production service-level objectives.

## Registry, revisions, and offline loading

`provider_registry.py` is the server-owned allowlist. API requests cannot select a
model. Each specification binds:

- provider and model ID;
- configured and resolved local revision;
- dimension and maximum sequence length;
- BGE query/passage transformation policy;
- normalization policy;
- supported devices and bounded batches;
- model policy version and resource estimate.

The resolver checks Hugging Face `refs` and `snapshots` metadata without loading
weights. If it cannot prove a concrete snapshot, it records
`unresolved-local-cache`; it never invents a revision.

Learned providers pass the resolved snapshot directory to sentence-transformers
with `local_files_only=True`. Missing packages, snapshots, invalid dimensions,
non-finite output, failed normalization, CUDA rejection, and loading failures are
typed unavailable states. No provider can initiate a download.

Models load on first use. A locked process-local least-recently-used cache retains
at most two models keyed by provider type, effective model identity, revision, and
device. Tests and diagnostics can explicitly clear it.

## Device and batching policy

Supported values are `cpu`, `cuda`, and `auto`; arbitrary CUDA device strings are
rejected. `auto` asks the existing local-AI admission service whether CUDA is
admitted. Otherwise it uses CPU. If an auto-admitted CUDA load fails before work
completes, the provider may make one CPU attempt. The actual device and safe
fallback classification are recorded.

Embedding and reranking batches are server-owned. Embeddings validate stable order,
exact count, 384 dimensions, finite values, and unit normalization within tolerance.
The cross-encoder receives at most 20 validated candidates. Its finite logits are
accepted as ranking scores and mapped monotonically into a bounded persisted score.

## Persistence, replay, and invalidation

No migration is required. Migration 16 already stores model identity, model
version, embedding policy, transformed text hash, dimension, vector hash, and vector
payload. The learned effective identity contains the provider, model, resolved
revision, transformation policy, dimension, and normalization policy.

Embedding reuse requires the exact chunk, transformed passage hash, provider
identity, revision-bound version, policy, and dimension. Retrieval fingerprints now
include the embedding and requested reranker identities. A changed provider,
revision, policy, or dimension cannot approximately replay.

Exact replay still validates current project identity, scope, plan, manifest,
repository state, source eligibility, invalidation state, and canonical artifact
hash. It returns before loading or calling either learned model.

## Phase 5B lineage

The immutable retrieval artifact and `RetrievalPhase5BEvidence` record:

- retrieval artifact ID and hash;
- exact project bindings;
- requested and effective provider identities;
- model IDs and resolved revisions;
- transformation and reranking policy;
- actual devices and fallback reason;
- evidence IDs, citation labels, relative paths, and line ranges;
- bounded untrusted excerpts.

The backend loads and revalidates the artifact before attaching it to the existing
Phase 5B envelope. Evidence remains advisory and untrusted. Better retrieval scores
do not confer approval, execution, mutation, verification, or lifecycle authority.

## Provider and chat APIs

`GET /chat/projects/{project_id}/rag/providers` provides lightweight readiness
without loading weights. Existing status and artifact routes remain unchanged.

Canonical project hydration includes bounded citation metadata for retrieval
artifacts. The chat-native project card shows the citation label, repository-relative
path, line range, bounded excerpt, retrieval mode, reranking fallback, stale state,
and advisory status. It contains no RAG approval or execution control.

## Evaluation

The tracked `astra_fixture_v1.json` corpus covers filenames, symbols, architecture,
approval bindings, replay, scope, migrations, provider fallback, API routes,
paraphrases, and an untrusted prompt-injection fixture.

The evaluator reports BM25, semantic, hybrid, deterministic reranking, and optional
learned reranking using Recall@1/3/5, MRR, nDCG@5, hit rates, zero-result rate,
latencies, fallback rate, stale rejection, and authority violations.

```bash
.venv/bin/python -m backend.app.project_retrieval.evaluation \
  --output-dir .work/rag-evaluation

ASTRA_RAG_EMBEDDING_DEVICE=cpu \
ASTRA_RAG_RERANKER_DEVICE=cpu \
.venv/bin/python -m backend.app.project_retrieval.evaluation \
  --learned --output-dir .work/rag-evaluation-learned
```

Generated reports are disposable and remain under ignored `.work/`.

## Configuration

```text
ASTRA_RAG_EMBEDDING_PROVIDER
ASTRA_RAG_EMBEDDING_MODEL
ASTRA_RAG_EMBEDDING_DEVICE
ASTRA_RAG_EMBEDDING_BATCH_SIZE
ASTRA_RAG_RERANKER_PROVIDER
ASTRA_RAG_RERANKER_MODEL
ASTRA_RAG_RERANKER_DEVICE
ASTRA_RAG_RERANKER_BATCH_SIZE
ASTRA_RAG_LOCAL_FILES_ONLY
ASTRA_RAG_PROVIDER_TIMEOUT_SECONDS
```

`ASTRA_RAG_LOCAL_FILES_ONLY` must remain true. Provider configuration is process
owned and is not mutable through project APIs.

## Smoke and optional integration

Install-free deterministic smoke:

```bash
.venv/bin/python -m backend.app.project_retrieval.smoke
```

Explicit cached-model smoke:

```bash
ASTRA_RUN_LOCAL_MODEL_TESTS=1 \
ASTRA_RAG_EMBEDDING_DEVICE=cpu \
ASTRA_RAG_RERANKER_DEVICE=cpu \
.venv/bin/python -m backend.app.project_retrieval.learned_smoke
```

The learned integration test is skipped unless
`ASTRA_RUN_LOCAL_MODEL_TESTS=1`. Absence of a cached model never triggers a
download.

## Limits

- Retrieval remains bounded brute-force comparison rather than a vector database.
- The evaluation corpus is fixture-scale and cannot establish statistical
  significance.
- No model is trained or fine-tuned.
- No external sources or provider APIs are supported.
- CPU/CUDA execution may produce small floating-point differences; exact replay
  uses the persisted result and never silently changes semantic model identity.
