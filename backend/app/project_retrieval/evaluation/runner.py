from __future__ import annotations

import json
from pathlib import Path
from time import perf_counter

from backend.app.project_control.contracts import canonical_json, content_hash
from backend.app.project_retrieval.bm25 import rank_bm25
from backend.app.project_retrieval.configuration import (
    retrieval_configuration_from_environment,
)
from backend.app.project_retrieval.evaluation.contracts import (
    EvaluationCorpus,
    EvaluationModeResult,
    QueryEvaluationResult,
    RetrievalEvaluationRun,
)
from backend.app.project_retrieval.evaluation.metrics import aggregate_metrics
from backend.app.project_retrieval.hybrid import hybrid_candidates
from backend.app.project_retrieval.providers import build_retrieval_providers
from backend.app.project_retrieval.reranking import (
    DeterministicLexicalReranker,
    normalize_rerank_scores,
    validate_rerank,
)
from backend.app.project_retrieval.semantic import (
    DeterministicEmbeddingProvider,
    cosine,
)
from backend.app.project_retrieval.contracts import RetrievalCandidate


DATASET = Path(__file__).parent / "data" / "astra_fixture_v1.json"


def load_corpus(path: Path = DATASET) -> EvaluationCorpus:
    return EvaluationCorpus.model_validate_json(path.read_text(encoding="utf-8"))


def run_evaluation(*, deterministic_only: bool = True) -> RetrievalEvaluationRun:
    corpus = load_corpus()
    deterministic = DeterministicEmbeddingProvider()
    modes = [
        _mode(corpus, "bm25", deterministic, reranker=None),
        _mode(corpus, "semantic", deterministic, reranker=None),
        _mode(corpus, "hybrid", deterministic, reranker=None),
        _mode(
            corpus,
            "hybrid_deterministic_rerank",
            deterministic,
            reranker=DeterministicLexicalReranker(),
        ),
    ]
    if not deterministic_only:
        try:
            embedding, reranker = build_retrieval_providers(
                retrieval_configuration_from_environment()
            )
            if not embedding.readiness().ready:
                raise RuntimeError(embedding.readiness().reason)
            if not reranker.readiness().ready:
                raise RuntimeError(reranker.readiness().reason)
            modes.extend((
                _mode(corpus, "hybrid_learned", embedding, reranker=None),
                _mode(
                    corpus, "hybrid_learned_rerank", embedding, reranker=reranker
                ),
            ))
        except Exception as exc:
            modes.append(EvaluationModeResult(
                mode="hybrid_learned_rerank",
                provider_identity="unavailable",
                available=False,
                exclusion_reason=str(exc)[:300],
            ))
    failures: list[str] = []
    if any(
        item.metrics
        and (
            item.metrics.prompt_injection_authority_violation_count != 0
            or item.metrics.stale_evidence_rejection_rate != 1.0
        )
        for item in modes
    ):
        failures.append("security_guardrail_failed")
    hybrid = next(
        (
            item for item in modes
            if item.mode == "hybrid_learned" and item.available
        ),
        next(item for item in modes if item.mode == "hybrid"),
    )
    learned = next(
        (item for item in modes if item.mode == "hybrid_learned_rerank" and item.available),
        None,
    )
    if (
        learned is not None
        and learned.metrics is not None
        and hybrid.metrics is not None
        and learned.metrics.mrr < hybrid.metrics.mrr
    ):
        failures.append("learned_reranker_reduced_fixture_mrr")
    material = {
        "corpus": corpus.model_dump(mode="json"),
        "deterministic_only": deterministic_only,
        "provider_identities": [item.provider_identity for item in modes],
    }
    return RetrievalEvaluationRun(
        run_id=f"rag-eval-{content_hash(material)[:24]}",
        corpus_id=corpus.corpus_id,
        deterministic_only=deterministic_only,
        modes=tuple(modes),
        guardrails_passed=not failures,
        guardrail_failures=tuple(failures),
    )


def _mode(corpus, mode, embedding, *, reranker):
    documents = {item.document_id: item.content for item in corpus.documents}
    passage_vectors = dict(zip(
        documents,
        embedding.embed_passages(tuple(documents.values())),
        strict=True,
    ))
    results = []
    for case in corpus.queries:
        started = perf_counter()
        bm25 = rank_bm25(case.query, documents, limit=len(documents))
        query_vector = embedding.embed_query(case.query)
        semantic = {
            document_id: cosine(query_vector, vector)
            for document_id, vector in passage_vectors.items()
        }
        if mode == "bm25":
            ranked = tuple(sorted(documents, key=lambda item: (-bm25.get(item, 0), item)))
            rerank_ms = 0.0
        elif mode == "semantic":
            ranked = tuple(sorted(documents, key=lambda item: (-semantic[item], item)))
            rerank_ms = 0.0
        else:
            candidates = hybrid_candidates(
                chunks={item: (item, item) for item in documents},
                bm25_scores=bm25,
                semantic_scores=semantic,
                limit=len(documents),
            )
            ranked = tuple(item.chunk_id for item in candidates)
            rerank_ms = 0.0
            if reranker is not None:
                rerank_started = perf_counter()
                pairs = tuple(
                    (candidate, documents[candidate.chunk_id])
                    for candidate in candidates[:20]
                )
                scores = normalize_rerank_scores(validate_rerank(
                    tuple(item[0] for item in pairs),
                    reranker.rerank(case.query, pairs),
                ))
                ranked = tuple(sorted(
                    scores,
                    key=lambda item: (
                        -scores[item],
                        next(c.rank_before_rerank for c in candidates if c.chunk_id == item),
                        item,
                    ),
                ))
                rerank_ms = (perf_counter() - rerank_started) * 1000.0
        results.append(QueryEvaluationResult(
            case_id=case.case_id,
            ranked_document_ids=ranked,
            relevant_document_ids=case.relevant_document_ids,
            latency_ms=(perf_counter() - started) * 1000.0,
            reranking_latency_ms=rerank_ms,
        ))
    typed = tuple(results)
    metrics = aggregate_metrics(
        typed,
        query_types={item.case_id: item.query_type for item in corpus.queries},
    )
    return EvaluationModeResult(
        mode=mode,
        provider_identity=embedding.identity + (
            f"+{reranker.identity}" if reranker is not None else ""
        ),
        available=True,
        query_results=typed,
        metrics=metrics,
    )


def write_reports(run: RetrievalEvaluationRun, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "rag-evaluation.json"
    markdown_path = output_dir / "rag-evaluation.md"
    json_path.write_text(
        json.dumps(run.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    rows = [
        "# Astra RAG Evaluation",
        "",
        "Fixture-scale results; they are not statistical production claims.",
        "",
        "| Mode | Available | Recall@1 | Recall@3 | MRR | nDCG@5 | Mean ms |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for item in run.modes:
        metrics = item.metrics
        rows.append(
            f"| {item.mode} | {'yes' if item.available else 'no'} | "
            + (
                f"{metrics.recall_at_1:.3f} | {metrics.recall_at_3:.3f} | "
                f"{metrics.mrr:.3f} | {metrics.ndcg_at_5:.3f} | "
                f"{metrics.mean_latency_ms:.3f} |"
                if metrics
                else f"n/a | n/a | n/a | n/a | n/a |"
            )
        )
    rows.extend([
        "",
        f"Security guardrails: {'passed' if run.guardrails_passed else 'failed'}.",
        "",
    ])
    markdown_path.write_text("\n".join(rows), encoding="utf-8")
    return json_path, markdown_path
