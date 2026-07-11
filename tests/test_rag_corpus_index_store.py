from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.rag.corpus_index_store import (
    build_corpus_index,
    corpus_index_files,
    corpus_index_status,
)


def _read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_corpus_index_full_rebuild_writes_manifest_and_chunks(
    tmp_path: Path,
):
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / "index"
    corpus_root.mkdir()
    (corpus_root / "notes.txt").write_text(
        "alpha\nbeta\ngamma\n",
        encoding="utf-8",
    )

    result = build_corpus_index(
        corpus_root,
        index_root=index_root,
        full_rebuild=True,
        max_chars=12,
        overlap_chars=2,
    )

    assert result["writes_performed"] is True
    assert result["embeddings_created"] is False
    assert result["full_rebuild"] is True
    assert result["source_file_count"] == 1
    assert result["indexed_file_count"] == 1
    assert result["chunk_count"] >= 1
    assert result["added_file_count"] == 1

    manifest = json.loads(
        (index_root / "manifest.json").read_text(encoding="utf-8")
    )
    chunks = _read_jsonl(index_root / "chunks.jsonl")

    assert manifest["chunk_count"] == len(chunks)
    assert manifest["embeddings_created"] is False
    assert chunks[0]["relative_path"] == "notes.txt"
    assert len(chunks[0]["source_sha256"]) == 64


def test_corpus_index_unchanged_rebuild_reuses_chunks(
    tmp_path: Path,
):
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / "index"
    corpus_root.mkdir()
    (corpus_root / "notes.txt").write_text(
        "alpha\nbeta\ngamma\n",
        encoding="utf-8",
    )

    first = build_corpus_index(
        corpus_root,
        index_root=index_root,
        full_rebuild=True,
        max_chars=12,
        overlap_chars=2,
    )
    first_chunks = _read_jsonl(index_root / "chunks.jsonl")

    second = build_corpus_index(
        corpus_root,
        index_root=index_root,
        max_chars=12,
        overlap_chars=2,
    )
    second_chunks = _read_jsonl(index_root / "chunks.jsonl")

    assert second["added_file_count"] == 0
    assert second["changed_file_count"] == 0
    assert second["unchanged_file_count"] == 1
    assert second["chunk_count"] == first["chunk_count"]
    assert [item["chunk_id"] for item in second_chunks] == [
        item["chunk_id"] for item in first_chunks
    ]
    assert len({item["chunk_id"] for item in second_chunks}) == len(
        second_chunks
    )


def test_corpus_index_changed_file_replaces_chunks(
    tmp_path: Path,
):
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / "index"
    corpus_root.mkdir()
    source = corpus_root / "notes.txt"
    source.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")

    build_corpus_index(
        corpus_root,
        index_root=index_root,
        full_rebuild=True,
        max_chars=12,
        overlap_chars=2,
    )
    first_chunk_ids = {
        item["chunk_id"] for item in _read_jsonl(index_root / "chunks.jsonl")
    }

    source.write_text(
        "alpha\nchanged beta\ngamma\n",
        encoding="utf-8",
    )
    result = build_corpus_index(
        corpus_root,
        index_root=index_root,
        max_chars=12,
        overlap_chars=2,
    )
    second_chunk_ids = {
        item["chunk_id"] for item in _read_jsonl(index_root / "chunks.jsonl")
    }

    assert result["added_file_count"] == 0
    assert result["changed_file_count"] == 1
    assert result["unchanged_file_count"] == 0
    assert first_chunk_ids != second_chunk_ids


def test_corpus_index_deleted_file_is_removed(
    tmp_path: Path,
):
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / "index"
    corpus_root.mkdir()
    (corpus_root / "keep.txt").write_text("keep me", encoding="utf-8")
    deleted = corpus_root / "delete.txt"
    deleted.write_text("delete me", encoding="utf-8")

    build_corpus_index(
        corpus_root,
        index_root=index_root,
        full_rebuild=True,
    )

    deleted.unlink()
    result = build_corpus_index(
        corpus_root,
        index_root=index_root,
    )
    files = corpus_index_files(index_root)

    assert result["deleted_file_count"] == 1
    assert result["deleted_files"] == ["delete.txt"]
    assert [item["relative_path"] for item in files["files"]] == [
        "keep.txt"
    ]
    assert all(
        item["relative_path"] == "keep.txt"
        for item in _read_jsonl(index_root / "chunks.jsonl")
    )


def test_corpus_index_malformed_index_recovers_on_build(
    tmp_path: Path,
):
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / "index"
    corpus_root.mkdir()
    index_root.mkdir()
    (corpus_root / "notes.txt").write_text("useful text", encoding="utf-8")
    (index_root / "manifest.json").write_text("{bad json", encoding="utf-8")

    malformed = corpus_index_status(index_root)

    assert malformed["status"] == "malformed"

    result = build_corpus_index(
        corpus_root,
        index_root=index_root,
    )
    recovered = corpus_index_status(index_root)

    assert result["recovered_from_malformed_index"] is True
    assert result["malformed_index_reason"]
    assert recovered["status"] == "ready"
    assert recovered["chunk_count"] == result["chunk_count"]


def test_corpus_index_endpoints_build_status_and_files(
    tmp_path: Path,
):
    corpus_root = tmp_path / "astra_corpus"
    corpus_root.mkdir()
    (corpus_root / "notes.txt").write_text(
        "alpha\nbeta\n",
        encoding="utf-8",
    )

    with TestClient(
        create_app(tmp_path / "app.db", workspace_root=tmp_path)
    ) as client:
        missing = client.get("/rag/corpus/index/status")
        assert missing.status_code == 200
        assert missing.json()["status"] == "missing"

        build = client.post(
            "/rag/corpus/index/build",
            json={
                "full_rebuild": True,
                "max_chars": 100,
                "overlap_chars": 10,
            },
        )
        assert build.status_code == 200

        build_data = build.json()
        assert build_data["writes_performed"] is True
        assert build_data["embeddings_created"] is False
        assert build_data["source_file_count"] == 1
        assert build_data["indexed_file_count"] == 1

        status = client.get("/rag/corpus/index/status")
        assert status.status_code == 200
        assert status.json()["status"] == "ready"
        assert status.json()["chunk_count"] == build_data["chunk_count"]

        files = client.get("/rag/corpus/index/files")
        assert files.status_code == 200

        file_data = files.json()
        assert file_data["status"] == "ready"
        assert file_data["files"][0]["relative_path"] == "notes.txt"
        assert file_data["files"][0]["source_status"] == "added"
