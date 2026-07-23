from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.project_api.routes import build_canonical_project_response
from backend.app.project_control.contracts import content_hash
from backend.app.project_control.project_service import CanonicalProjectService
from backend.app.project_retrieval.learned import (
    CrossEncoderReranker,
    SentenceTransformerEmbeddingProvider,
    clear_learned_model_cache,
)
from backend.app.project_retrieval.provider_registry import (
    LocalModelResolution,
    ProviderDevice,
    embedding_spec,
    reranker_spec,
)
from backend.app.project_retrieval.routes import create_project_retrieval_router
from backend.app.project_retrieval.service import (
    ProjectRetrievalError,
    ProjectRetrievalService,
)
from tests.test_rag_integration import _fixture, _request


class _Array:
    def __init__(self, values):
        self.values = values

    def tolist(self):
        return self.values


class _EmbeddingModel:
    def encode(self, texts, **kwargs):
        vector = [0.0] * 384
        vector[0] = 1.0
        return _Array([vector[:] for _ in texts])


class _RerankerModel:
    def predict(self, pairs, **kwargs):
        return _Array([float(len(pairs) - index) for index in range(len(pairs))])


def _learned_service(tmp_path, database, control, artifacts, revision="revision-1"):
    clear_learned_model_cache()
    embedding_model = embedding_spec("BAAI/bge-small-en-v1.5")
    reranker_model = reranker_spec("cross-encoder/ms-marco-MiniLM-L6-v2")
    embedding_snapshot = tmp_path / f"embedding-{revision}"
    reranker_snapshot = tmp_path / f"reranker-{revision}"
    embedding_snapshot.mkdir()
    reranker_snapshot.mkdir()
    embedding = SentenceTransformerEmbeddingProvider(
        embedding_model,
        LocalModelResolution(
            model_id=embedding_model.model_id,
            configured_revision="main",
            locally_cached=True,
            resolved_revision=revision,
            snapshot_path=embedding_snapshot.as_posix(),
            effective_identity=content_hash([embedding_model.model_id, revision]),
        ),
        requested_device=ProviderDevice.CPU,
        loader=lambda path, device: _EmbeddingModel(),
    )
    reranker = CrossEncoderReranker(
        reranker_model,
        LocalModelResolution(
            model_id=reranker_model.model_id,
            configured_revision="main",
            locally_cached=True,
            resolved_revision=revision,
            snapshot_path=reranker_snapshot.as_posix(),
            effective_identity=content_hash([reranker_model.model_id, revision]),
        ),
        requested_device=ProviderDevice.CPU,
        loader=lambda path, device: _RerankerModel(),
    )
    return (
        ProjectRetrievalService(
            database,
            control,
            artifacts,
            embedding_provider=embedding,
            reranker=reranker,
        ),
        embedding,
        reranker,
    )


def test_learned_identity_persistence_lineage_and_exact_replay(tmp_path: Path) -> None:
    _root, _source, database, control, artifacts, _old, binding = _fixture(tmp_path)
    retrieval, embedding, reranker = _learned_service(
        tmp_path, database, control, artifacts
    )
    from backend.app.project_retrieval.contracts import CorpusIngestionRequest
    retrieval.ingest_project_corpus(
        CorpusIngestionRequest(**binding, idempotency_key="learned-ingest")
    )
    request = _request(binding)
    artifact = retrieval.retrieve(request)
    calls = (embedding.call_count, reranker.call_count)
    replay = retrieval.retrieve(request)

    assert artifact.provider_trace is not None
    assert artifact.provider_trace.embedding_model_id == "BAAI/bge-small-en-v1.5"
    assert artifact.provider_trace.embedding_resolved_revision == "revision-1"
    assert artifact.provider_trace.reranker_model_id.endswith("ms-marco-MiniLM-L6-v2")
    assert (embedding.call_count, reranker.call_count) == calls
    assert replay.replayed is True
    phase5b = retrieval.phase5b_evidence(artifact.artifact_id, request)
    assert phase5b.provider_trace == artifact.provider_trace
    assert phase5b.advisory_only is True
    assert phase5b.has_execution_authority is False


def test_changed_learned_revision_cannot_approximately_replay(tmp_path: Path) -> None:
    _root, _source, database, control, artifacts, _old, binding = _fixture(tmp_path)
    first, _embedding, _reranker = _learned_service(
        tmp_path, database, control, artifacts, "revision-1"
    )
    from backend.app.project_retrieval.contracts import CorpusIngestionRequest
    first.ingest_project_corpus(
        CorpusIngestionRequest(**binding, idempotency_key="learned-ingest")
    )
    request = _request(binding)
    first.retrieve(request)
    second, _embedding2, _reranker2 = _learned_service(
        tmp_path, database, control, artifacts, "revision-2"
    )
    with pytest.raises(ProjectRetrievalError, match="replay_mismatch"):
        second.retrieve(request)


def test_provider_api_and_canonical_hydration_expose_advisory_citations(
    tmp_path: Path,
) -> None:
    _root, _source, database, control, artifacts, _old, binding = _fixture(tmp_path)
    retrieval, _embedding, _reranker = _learned_service(
        tmp_path, database, control, artifacts
    )
    from backend.app.project_retrieval.contracts import CorpusIngestionRequest
    retrieval.ingest_project_corpus(
        CorpusIngestionRequest(**binding, idempotency_key="learned-ingest")
    )
    retrieval.retrieve(_request(binding))
    application = FastAPI()
    application.include_router(create_project_retrieval_router(retrieval))
    provider_response = TestClient(application).get(
        f"/chat/projects/{binding['project_id']}/rag/providers"
    )
    assert provider_response.status_code == 200
    assert len(provider_response.json()["items"]) == 2
    assert all(item["advisory_only"] for item in provider_response.json()["items"])

    canonical = CanonicalProjectService(control, artifacts)
    hydrated = build_canonical_project_response(
        canonical, str(binding["project_id"]), retrieval=retrieval
    )
    rag = next(item for item in hydrated.artifacts if item.artifact_type == "retrieval_evidence")
    assert rag.retrieval_evidence
    assert rag.retrieval_evidence[0].relative_path == "src/app.py"
    assert rag.advisory_only is True
    assert rag.invalidated is False


def test_phase6_schema_already_carries_exact_learned_identity_without_migration(
    tmp_path: Path,
) -> None:
    import sqlite3

    _root, _source, database, _control, _artifacts, _old, _binding = _fixture(tmp_path)
    with sqlite3.connect(database) as connection:
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(rag_embeddings)")
        }
    assert {
        "model_identity",
        "model_version",
        "embedding_policy_version",
        "chunk_text_hash",
        "dimensions",
        "embedding_hash",
        "vector_json",
    } <= columns


def test_local_ai_doctor_can_report_rag_readiness_without_loading_weights(
    tmp_path: Path,
) -> None:
    from datetime import datetime, timezone

    from backend.app.local_ai.contracts import Capability, CapabilityStatus
    from backend.app.local_ai.service import LocalAIService

    _root, _source, database, _control, _artifacts, _old, _binding = _fixture(tmp_path)
    local_ai = LocalAIService(database)
    local_ai.initialize()
    local_ai.set_additional_capability_probe(lambda: (
        Capability(
            capability_id="rag_embedding_provider",
            status=CapabilityStatus.READY,
            version="revision-1",
            details={"weights_loaded": False, "model_present_locally": True},
            probed_at=datetime.now(timezone.utc),
            provenance={"network_used": False},
        ),
    ))
    report = local_ai.capability_report(refresh=True)
    rag = next(
        item for item in report.capabilities
        if item.capability_id == "rag_embedding_provider"
    )
    assert rag.status == CapabilityStatus.READY
    assert rag.details["weights_loaded"] is False
    assert rag.provenance["network_used"] is False
