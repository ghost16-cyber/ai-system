from __future__ import annotations

from datetime import datetime, timezone

from backend.app.local_ai.contracts import (
    AdmissionOutcome,
    Capability,
    CapabilityStatus,
    HardwareAdmissionRequest,
)
from backend.app.local_ai.service import LocalAIService
from backend.app.project_retrieval.configuration import RetrievalProviderConfiguration
from backend.app.project_retrieval.learned import (
    CrossEncoderReranker,
    SentenceTransformerEmbeddingProvider,
)
from backend.app.project_retrieval.provider_registry import (
    embedding_spec,
    reranker_spec,
    resolve_local_model,
)
from backend.app.project_retrieval.reranking import (
    DeterministicLexicalReranker,
    UnavailableReranker,
)
from backend.app.project_retrieval.semantic import (
    DeterministicEmbeddingProvider,
    UnavailableEmbeddingProvider,
)


def build_retrieval_providers(
    configuration: RetrievalProviderConfiguration,
    *,
    local_ai: LocalAIService | None = None,
):
    cuda_admitted = (
        (lambda: _cuda_admitted(local_ai))
        if local_ai is not None
        else (lambda: False)
    )
    if configuration.embedding_provider == "sentence_transformer":
        specification = embedding_spec(configuration.embedding_model)
        resolution = resolve_local_model(specification)
        if (
            not resolution.locally_cached
            and configuration.embedding_model == "BAAI/bge-small-en-v1.5"
        ):
            fallback_specification = embedding_spec(
                "sentence-transformers/all-MiniLM-L6-v2"
            )
            fallback_resolution = resolve_local_model(fallback_specification)
            if fallback_resolution.locally_cached:
                specification = fallback_specification
                resolution = fallback_resolution
        embedding = SentenceTransformerEmbeddingProvider(
            specification,
            resolution,
            requested_device=configuration.embedding_device,
            batch_size=configuration.embedding_batch_size,
            cuda_admitted=cuda_admitted,
            timeout_seconds=configuration.provider_timeout_seconds,
        )
    elif configuration.embedding_provider == "deterministic":
        embedding = DeterministicEmbeddingProvider()
    else:
        embedding = UnavailableEmbeddingProvider()

    if configuration.reranker_provider == "cross_encoder":
        specification = reranker_spec(configuration.reranker_model)
        reranker = CrossEncoderReranker(
            specification,
            resolve_local_model(specification),
            requested_device=configuration.reranker_device,
            batch_size=configuration.reranker_batch_size,
            cuda_admitted=cuda_admitted,
            timeout_seconds=configuration.provider_timeout_seconds,
        )
    elif configuration.reranker_provider == "deterministic":
        reranker = DeterministicLexicalReranker()
    else:
        reranker = UnavailableReranker()
    return embedding, reranker


def _cuda_admitted(local_ai: LocalAIService) -> bool:
    decision = local_ai.admission_preview(HardwareAdmissionRequest(
        workload_class="rag_learned_retrieval",
        model_profile_id="rag-learned-provider",
        estimated_model_bytes=256 * 1024**2,
        requested_context=512,
        estimated_kv_bytes_per_token=0,
        requested_output_tokens=1,
        allow_cpu_fallback=True,
        prefer_gpu=True,
    ))
    return decision.outcome in {
        AdmissionOutcome.GPU,
        AdmissionOutcome.REDUCED_CONTEXT,
    }


def rag_provider_capabilities(embedding, reranker) -> tuple[Capability, ...]:
    """Bounded capability probe for the learned RAG providers: local cache
    metadata only, never loads weights or touches the network. Shared by the
    LocalAIService capability-probe registration and RuntimeManager's
    ProviderAdapter so there is exactly one implementation."""
    values: list[Capability] = []
    for capability_id, provider in (
        ("rag_embedding_provider", embedding),
        ("rag_reranker_provider", reranker),
    ):
        readiness_method = getattr(provider, "readiness", None)
        if callable(readiness_method):
            readiness = readiness_method()
            values.append(Capability(
                capability_id=capability_id,
                status=(
                    CapabilityStatus.READY
                    if readiness.ready
                    else CapabilityStatus.UNAVAILABLE
                ),
                version=readiness.resolved_revision,
                details=readiness.model_dump(mode="json"),
                reason=readiness.reason,
                probed_at=datetime.now(timezone.utc),
                provenance={
                    "probe": "rag_local_cache_metadata",
                    "weights_loaded": False,
                    "network_used": False,
                },
            ))
    return tuple(values)


__all__ = ["build_retrieval_providers", "rag_provider_capabilities"]
