from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.rag.corpus_inventory import scan_corpus
from backend.app.rag.corpus_text_extractor import (
    extract_corpus_file,
    extract_indexable_corpus,
)


def test_extracts_only_index_eligible_utf8_text(tmp_path: Path):
    (tmp_path / "main.py").write_text(
        "print('hello')\r\nprint('world')\x00",
        encoding="utf-8",
    )
    (tmp_path / "empty.txt").write_text("", encoding="utf-8")
    (tmp_path / "model.pt").write_text(
        "not safe",
        encoding="utf-8",
    )

    result = extract_indexable_corpus(
        tmp_path,
        include_text=True,
    )

    assert result["mode"] == "read_only_extraction"
    assert result["writes_performed"] is False
    assert result["embeddings_created"] is False
    assert result["chunking_performed"] is False

    assert result["eligible_file_count"] == 1
    assert result["processed_file_count"] == 1
    assert result["extracted_file_count"] == 1
    assert result["failed_file_count"] == 0

    item = result["files"][0]

    assert item["relative_path"] == "main.py"
    assert item["extraction_status"] == "extracted"
    assert item["extraction_reason"] == "ok"
    assert item["text"] == "print('hello')\nprint('world')"
    assert item["line_count"] == 2


def test_extractor_rejects_path_outside_corpus_root(
    tmp_path: Path,
):
    outside = tmp_path.parent / "outside.py"
    outside.write_text("print('outside')", encoding="utf-8")

    record = {
        "relative_path": "../outside.py",
        "extension": ".py",
        "size_bytes": outside.stat().st_size,
        "index_eligible": True,
    }

    result = extract_corpus_file(
        tmp_path,
        record,
        include_text=True,
    )

    assert result["extraction_status"] == "failed"
    assert result["extraction_reason"] == (
        "path_outside_corpus_root"
    )
    assert result["text"] is None


def test_extractor_reports_unsupported_binary_formats(
    tmp_path: Path,
):
    document = tmp_path / "report.pdf"
    document.write_bytes(b"%PDF-test")

    inventory = scan_corpus(tmp_path)
    record = inventory["files"][0]

    assert record["index_eligible"] is True

    result = extract_corpus_file(
        tmp_path,
        record,
        include_text=True,
    )

    assert result["extraction_status"] == "skipped"
    assert result["extraction_reason"] == (
        "unsupported_binary_format"
    )
    assert result["text"] is None


def test_extractor_truncates_large_text(tmp_path: Path):
    (tmp_path / "large.md").write_text(
        "abcdefghij",
        encoding="utf-8",
    )

    result = extract_indexable_corpus(
        tmp_path,
        include_text=True,
        max_characters=5,
    )

    item = result["files"][0]

    assert item["extraction_status"] == "extracted"
    assert item["text"] == "abcde"
    assert item["character_count"] == 5
    assert item["truncated"] is True


def test_extractor_can_hide_text_from_response(tmp_path: Path):
    (tmp_path / "notes.txt").write_text(
        "private corpus text",
        encoding="utf-8",
    )

    result = extract_indexable_corpus(
        tmp_path,
        include_text=False,
    )

    item = result["files"][0]

    assert item["extraction_status"] == "extracted"
    assert item["character_count"] == len(
        "private corpus text"
    )
    assert item["text"] is None


def test_rag_corpus_extraction_preview_endpoint():
    client = TestClient(app)

    response = client.get(
        "/rag/corpus/extraction-preview",
        params={"limit": 5},
    )

    assert response.status_code == 200

    data = response.json()

    assert data["mode"] == "read_only_extraction"
    assert data["writes_performed"] is False
    assert data["embeddings_created"] is False
    assert data["chunking_performed"] is False
    assert "eligible_file_count" in data
    assert "processed_file_count" in data
    assert "status_counts" in data
    assert "reason_counts" in data
    assert "files" in data
