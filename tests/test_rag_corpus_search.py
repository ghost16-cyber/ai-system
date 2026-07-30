from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.rag.corpus_search import search_corpus_vectors
from backend.app.rag.corpus_vector_store import (
    CorpusVectorStoreError,
    IncompatibleEmbeddingConfigurationError,
    build_corpus_vectors,
)
from backend.app.rag.deterministic_embeddings import DeterministicEmbeddingProvider


def _write_index(root: Path) -> None:
    chunks = [
        {"chunk_id": "report", "relative_path": "report.txt", "source_sha256": "one", "chunk_index": 0, "text": "assignment report generation report", "start_line": 1, "end_line": 1, "extension": ".txt"},
        {"chunk_id": "server", "relative_path": "server.txt", "source_sha256": "two", "chunk_index": 0, "text": "backend server database", "start_line": 1, "end_line": 1, "extension": ".txt"},
        {"chunk_id": "mixed", "relative_path": "mixed.txt", "source_sha256": "three", "chunk_index": 0, "text": "assignment backend", "start_line": 1, "end_line": 1, "extension": ".txt"},
    ]
    root.mkdir(parents=True, exist_ok=True)
    (root / "manifest.json").write_text(json.dumps({"schema_version": 1, "updated_at": "now", "chunk_count": len(chunks)}), encoding="utf-8")
    (root / "chunks.jsonl").write_text("".join(json.dumps(item) + "\n" for item in chunks), encoding="utf-8")


def test_similarity_ordering_top_k_minimum_score_and_source_filter(tmp_path: Path) -> None:
    index_root = tmp_path / "index"
    vector_root = tmp_path / "vectors"
    provider = DeterministicEmbeddingProvider()
    _write_index(index_root)
    build_corpus_vectors(provider, index_root=index_root, vector_root=vector_root)

    ranked = search_corpus_vectors("assignment report", provider, vector_root=vector_root, top_k=2)
    filtered = search_corpus_vectors("assignment report", provider, vector_root=vector_root, source_path="mixed.txt")
    strict = search_corpus_vectors("assignment report", provider, vector_root=vector_root, minimum_score=0.99)

    assert ranked["results"][0]["chunk_id"] == "report"
    assert len(ranked["results"]) == 2
    assert [item["score"] for item in ranked["results"]] == sorted(
        [item["score"] for item in ranked["results"]], reverse=True
    )
    assert [item["source_path"] for item in filtered["results"]] == ["mixed.txt"]
    assert all(item["score"] >= 0.99 for item in strict["results"])


def test_search_handles_invalid_top_k_and_missing_store(tmp_path: Path) -> None:
    provider = DeterministicEmbeddingProvider()
    with pytest.raises(ValueError, match="top_k"):
        search_corpus_vectors("query", provider, vector_root=tmp_path, top_k=0)
    with pytest.raises(CorpusVectorStoreError, match="missing"):
        search_corpus_vectors("query", provider, vector_root=tmp_path)


def test_search_rejects_incompatible_embedding_configuration(tmp_path: Path) -> None:
    index_root = tmp_path / "index"
    vector_root = tmp_path / "vectors"
    _write_index(index_root)
    build_corpus_vectors(
        DeterministicEmbeddingProvider(16),
        index_root=index_root,
        vector_root=vector_root,
    )

    with pytest.raises(IncompatibleEmbeddingConfigurationError, match="incompatible"):
        search_corpus_vectors(
            "assignment",
            DeterministicEmbeddingProvider(32),
            vector_root=vector_root,
        )


def test_corpus_embedding_and_search_api_endpoints(tmp_path: Path) -> None:
    index_root = tmp_path / "data" / "rag" / "corpus_index"
    _write_index(index_root)

    with TestClient(create_app(tmp_path / "app.db", workspace_root=tmp_path)) as client:
        missing = client.get("/rag/corpus/embeddings/status")
        build = client.post("/rag/corpus/embeddings/build", json={"full_rebuild": False})
        status = client.get("/rag/corpus/embeddings/status")
        files = client.get("/rag/corpus/embeddings/files")
        search = client.post(
            "/rag/corpus/search",
            json={"query": "assignment report", "top_k": 1, "minimum_score": 0.0, "source_path": None},
        )

    assert missing.json()["status"] == "missing"
    assert build.status_code == 200
    assert build.json()["embedded_new"] == 3
    assert status.json()["status"] == "ready"
    assert status.json()["vector_count"] == 3
    assert files.json()["status"] == "ready"
    assert len(files.json()["files"]) == 3
    assert search.status_code == 200
    assert search.json()["results"][0]["chunk_id"] == "report"
