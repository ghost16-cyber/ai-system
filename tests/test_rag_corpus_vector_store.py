from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.app.rag.corpus_vector_store import (
    IncompatibleEmbeddingConfigurationError,
    build_corpus_vectors,
    corpus_vector_files,
    corpus_vector_status,
)
from backend.app.rag.deterministic_embeddings import DeterministicEmbeddingProvider


class CountingProvider(DeterministicEmbeddingProvider):
    def __init__(self, dimension: int = 32) -> None:
        super().__init__(dimension)
        self.embedded_texts: list[str] = []

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        self.embedded_texts.extend(texts)
        return super().embed_texts(texts)


def _chunk(chunk_id: str, path: str, index: int, text: str, source_hash: str = "source-a") -> dict:
    return {
        "chunk_id": chunk_id,
        "relative_path": path,
        "source_sha256": source_hash,
        "chunk_index": index,
        "text": text,
        "start_line": index + 1,
        "end_line": index + 1,
        "extension": ".txt",
    }


def _write_index(root: Path, chunks: list[dict]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "manifest.json").write_text(
        json.dumps({"schema_version": 1, "updated_at": "now", "chunk_count": len(chunks)}),
        encoding="utf-8",
    )
    (root / "chunks.jsonl").write_text(
        "".join(json.dumps(chunk) + "\n" for chunk in chunks),
        encoding="utf-8",
    )


def _read_vectors(root: Path) -> list[dict]:
    return [json.loads(line) for line in (root / "vectors.jsonl").read_text(encoding="utf-8").splitlines() if line]


def test_initial_vector_build_and_unchanged_reuse(tmp_path: Path) -> None:
    index_root = tmp_path / "index"
    vector_root = tmp_path / "vectors"
    chunks = [_chunk("a", "a.txt", 0, "alpha"), _chunk("b", "a.txt", 1, "beta")]
    _write_index(index_root, chunks)
    provider = CountingProvider()

    first = build_corpus_vectors(provider, index_root=index_root, vector_root=vector_root)
    first_vectors = _read_vectors(vector_root)
    provider.embedded_texts.clear()
    second = build_corpus_vectors(provider, index_root=index_root, vector_root=vector_root)

    assert first["embedded_new"] == 2
    assert first["total_vectors"] == 2
    assert second["reused_unchanged"] == 2
    assert second["embedded_new"] == 0
    assert provider.embedded_texts == []
    assert _read_vectors(vector_root) == first_vectors


def test_changed_chunk_is_reembedded_and_deleted_chunk_removed(tmp_path: Path) -> None:
    index_root = tmp_path / "index"
    vector_root = tmp_path / "vectors"
    _write_index(index_root, [_chunk("a", "a.txt", 0, "alpha"), _chunk("b", "a.txt", 1, "beta")])
    build_corpus_vectors(CountingProvider(), index_root=index_root, vector_root=vector_root)

    provider = CountingProvider()
    _write_index(index_root, [_chunk("a2", "a.txt", 0, "alpha changed", "source-b")])
    result = build_corpus_vectors(provider, index_root=index_root, vector_root=vector_root)

    assert result["embedded_changed"] == 1
    assert result["deleted"] == 1
    assert result["total_vectors"] == 1
    assert provider.embedded_texts == ["alpha changed"]
    assert [item["chunk_id"] for item in _read_vectors(vector_root)] == ["a2"]


def test_build_never_creates_duplicate_vectors_and_full_rebuild_embeds_all(tmp_path: Path) -> None:
    index_root = tmp_path / "index"
    vector_root = tmp_path / "vectors"
    _write_index(index_root, [_chunk("a", "a.txt", 0, "alpha"), _chunk("b", "b.txt", 0, "beta")])
    provider = CountingProvider()
    build_corpus_vectors(provider, index_root=index_root, vector_root=vector_root)
    provider.embedded_texts.clear()

    result = build_corpus_vectors(provider, index_root=index_root, vector_root=vector_root, full_rebuild=True)
    records = _read_vectors(vector_root)

    assert result["embedded_new"] == 2
    assert result["reused_unchanged"] == 0
    assert provider.embedded_texts == ["alpha", "beta"]
    assert len({item["chunk_id"] for item in records}) == len(records) == 2


def test_embedding_configuration_mismatch_requires_full_rebuild(tmp_path: Path) -> None:
    index_root = tmp_path / "index"
    vector_root = tmp_path / "vectors"
    _write_index(index_root, [_chunk("a", "a.txt", 0, "alpha")])
    build_corpus_vectors(CountingProvider(16), index_root=index_root, vector_root=vector_root)

    with pytest.raises(IncompatibleEmbeddingConfigurationError, match="full_rebuild=true"):
        build_corpus_vectors(CountingProvider(32), index_root=index_root, vector_root=vector_root)

    rebuilt = build_corpus_vectors(
        CountingProvider(32), index_root=index_root, vector_root=vector_root, full_rebuild=True
    )
    assert rebuilt["embedding"]["dimension"] == 32


@pytest.mark.parametrize(
    ("filename", "content"),
    [("manifest.json", "{bad json"), ("vectors.jsonl", "{bad json\n")],
)
def test_malformed_vector_store_is_reported_and_replaced(
    tmp_path: Path, filename: str, content: str
) -> None:
    index_root = tmp_path / "index"
    vector_root = tmp_path / "vectors"
    _write_index(index_root, [_chunk("a", "a.txt", 0, "alpha")])
    build_corpus_vectors(CountingProvider(), index_root=index_root, vector_root=vector_root)
    (vector_root / filename).write_text(content, encoding="utf-8")

    assert corpus_vector_status(vector_root)["status"] == "malformed"
    recovered = build_corpus_vectors(CountingProvider(), index_root=index_root, vector_root=vector_root)

    assert recovered["recovered_from_malformed_store"] is True
    assert corpus_vector_status(vector_root)["status"] == "ready"


def test_empty_corpus_index_produces_valid_empty_store(tmp_path: Path) -> None:
    index_root = tmp_path / "index"
    vector_root = tmp_path / "vectors"
    _write_index(index_root, [])

    result = build_corpus_vectors(CountingProvider(), index_root=index_root, vector_root=vector_root)
    files = corpus_vector_files(vector_root)

    assert result["total_vectors"] == 0
    assert corpus_vector_status(vector_root)["status"] == "ready"
    assert files["files"] == []
