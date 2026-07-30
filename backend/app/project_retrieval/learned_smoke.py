from __future__ import annotations

import json
import os
from time import perf_counter

from backend.app.project_retrieval.configuration import (
    retrieval_configuration_from_environment,
)
from backend.app.project_retrieval.providers import build_retrieval_providers
from backend.app.project_retrieval.smoke import run_smoke


def run_learned_smoke() -> dict[str, object]:
    if os.getenv("ASTRA_RUN_LOCAL_MODEL_TESTS") != "1":
        raise RuntimeError("ASTRA_RUN_LOCAL_MODEL_TESTS=1 is required")
    configuration = retrieval_configuration_from_environment()
    embedding, reranker = build_retrieval_providers(configuration)
    embedding_status = embedding.readiness()
    reranker_status = reranker.readiness()
    if not embedding_status.ready:
        raise RuntimeError(embedding_status.reason or "embedding_provider_unavailable")
    if not reranker_status.ready:
        raise RuntimeError(reranker_status.reason or "reranker_provider_unavailable")
    started = perf_counter()
    result = run_smoke(embedding_provider=embedding, reranker=reranker)
    if result["provider_calls_before_replay"] != result["provider_calls_after_replay"]:
        raise RuntimeError("exact replay called a learned provider")
    result.update({
        "embedding_identity": embedding.identity,
        "embedding_revision": embedding.resolution.resolved_revision,
        "embedding_device": embedding.actual_device,
        "embedding_dimensions": embedding.dimensions,
        "reranker_identity": reranker.identity,
        "reranker_revision": reranker.resolution.resolved_revision,
        "reranker_device": reranker.actual_device,
        "duration_ms": (perf_counter() - started) * 1000.0,
        "advisory_only": True,
        "has_approval_authority": False,
        "has_execution_authority": False,
        "has_mutation_authority": False,
    })
    return result


def main() -> int:
    try:
        result = run_learned_smoke()
    except Exception as exc:
        print(json.dumps({
            "status": "failed",
            "failure_type": type(exc).__name__,
            "reason": str(exc)[:300],
        }, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
