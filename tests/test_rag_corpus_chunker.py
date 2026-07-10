from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.rag.corpus_chunker import (
    build_corpus_chunk_preview,
    chunk_text,
)


def test_chunk_text_is_deterministic():
    text = "line one\nline two\nline three\n"

    first = chunk_text(
        relative_path="notes.txt",
        extension=".txt",
        text=text,
        max_chars=15,
        overlap_chars=3,
    )
    second = chunk_text(
        relative_path="notes.txt",
        extension=".txt",
        text=text,
        max_chars=15,
        overlap_chars=3,
    )

    assert first == second
    assert all(len(item["chunk_id"]) == 64 for item in first)


def test_chunk_text_preserves_source_metadata():
    chunks = chunk_text(
        relative_path="src/main.py",
        extension=".py",
        text="a = 1\nb = 2\nc = 3\n",
        max_chars=10,
        overlap_chars=2,
    )

    assert len(chunks) >= 2

    first = chunks[0]

    assert first["relative_path"] == "src/main.py"
    assert first["extension"] == ".py"
    assert first["chunk_index"] == 0
    assert first["start_line"] == 1
    assert first["end_line"] >= 1
    assert first["character_count"] == len(first["text"])


def test_chunk_text_enforces_maximum_size():
    chunks = chunk_text(
        relative_path="large.txt",
        extension=".txt",
        text="x" * 25,
        max_chars=10,
        overlap_chars=2,
    )

    assert len(chunks) == 3
    assert all(
        item["character_count"] <= 10
        for item in chunks
    )


def test_chunk_text_validates_configuration():
    try:
        chunk_text(
            relative_path="notes.txt",
            extension=".txt",
            text="hello",
            max_chars=10,
            overlap_chars=10,
        )
    except ValueError as error:
        assert "smaller than max_chars" in str(error)
    else:
        raise AssertionError("Expected ValueError")


def test_chunk_preview_uses_extracted_files_only(
    tmp_path: Path,
):
    (tmp_path / "notes.txt").write_text(
        "first line\nsecond line\nthird line\n",
        encoding="utf-8",
    )
    (tmp_path / "report.pdf").write_bytes(b"%PDF-test")
    (tmp_path / "model.pt").write_text(
        "unsafe",
        encoding="utf-8",
    )

    result = build_corpus_chunk_preview(
        tmp_path,
        include_text=True,
        max_chars=16,
        overlap_chars=4,
    )

    assert result["mode"] == "read_only_chunk_preview"
    assert result["writes_performed"] is False
    assert result["embeddings_created"] is False

    chunk_paths = {
        item["relative_path"]
        for item in result["chunks"]
    }

    assert chunk_paths == {"notes.txt"}
    assert result["chunked_file_count"] == 1

    skipped = {
        item["relative_path"]: item["reason"]
        for item in result["skipped_files"]
    }

    assert skipped["report.pdf"] == (
        "unsupported_binary_format"
    )


def test_chunk_preview_can_hide_chunk_text(
    tmp_path: Path,
):
    (tmp_path / "notes.txt").write_text(
        "some useful text",
        encoding="utf-8",
    )

    result = build_corpus_chunk_preview(
        tmp_path,
        include_text=False,
    )

    assert result["chunk_count"] == 1
    assert result["chunks"][0]["text"] is None


def test_rag_corpus_chunk_preview_endpoint():
    client = TestClient(app)

    response = client.get(
        "/rag/corpus/chunk-preview",
        params={
            "file_limit": 5,
            "max_chars": 1000,
            "overlap_chars": 100,
        },
    )

    assert response.status_code == 200

    data = response.json()

    assert data["mode"] == "read_only_chunk_preview"
    assert data["writes_performed"] is False
    assert data["embeddings_created"] is False
    assert "chunk_count" in data
    assert "chunks_per_file" in data
    assert "chunks" in data
    assert "skipped_files" in data
