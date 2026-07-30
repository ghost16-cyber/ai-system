from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.rag.corpus_index_preview import (
    build_corpus_index_preview,
)


def test_index_preview_is_dry_run_and_estimates_eligible_files(
    tmp_path: Path,
):
    (tmp_path / "small.py").write_text(
        "x" * 10,
        encoding="utf-8",
    )
    (tmp_path / "large.md").write_text(
        "x" * 4100,
        encoding="utf-8",
    )
    (tmp_path / "empty.txt").write_text(
        "",
        encoding="utf-8",
    )
    (tmp_path / "model.pt").write_text(
        "unsafe model data",
        encoding="utf-8",
    )

    result = build_corpus_index_preview(tmp_path)

    assert result["mode"] == "dry_run"
    assert result["writes_performed"] is False
    assert result["embeddings_created"] is False

    assert result["total_files"] == 4
    assert result["indexable_files"] == 2
    assert result["index_skipped_files"] == 1
    assert result["ignored_files"] == 1
    assert result["excluded_file_count"] == 2

    assert result["estimated_index_chunk_count"] == 3

    indexed = {
        item["relative_path"]: item
        for item in result["files"]
    }
    excluded = {
        item["relative_path"]: item
        for item in result["excluded_files"]
    }

    assert indexed["small.py"]["estimated_chunks"] == 1
    assert indexed["large.md"]["estimated_chunks"] == 2

    assert excluded["empty.txt"]["estimated_chunks"] == 0
    assert excluded["empty.txt"]["exclusion_reason"] == (
        "skipped_empty_file"
    )

    assert excluded["model.pt"]["estimated_chunks"] == 0
    assert excluded["model.pt"]["exclusion_reason"] == (
        "ignored_extension"
    )


def test_index_preview_preserves_eligibility_exclusion_reasons(
    tmp_path: Path,
):
    (tmp_path / "model_train.csv").write_text(
        "feature,label\n1,0\n",
        encoding="utf-8",
    )
    (tmp_path / "useful_metrics.csv").write_text(
        "metric,value\naccuracy,0.9\n",
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text(
        "SECRET=do-not-index",
        encoding="utf-8",
    )

    result = build_corpus_index_preview(tmp_path)

    indexed_paths = {
        item["relative_path"] for item in result["files"]
    }
    excluded = {
        item["relative_path"]: item
        for item in result["excluded_files"]
    }

    assert indexed_paths == {"useful_metrics.csv"}

    assert excluded["model_train.csv"]["exclusion_reason"] == (
        "skipped_generated_dataset"
    )
    assert excluded[".env"]["exclusion_reason"] == (
        "ignored_sensitive_env_file"
    )

    assert result["exclusion_reason_counts"] == {
        "ignored_sensitive_env_file": 1,
        "skipped_generated_dataset": 1,
    }


def test_rag_corpus_index_preview_endpoint():
    client = TestClient(app)

    response = client.get("/rag/corpus/index-preview")

    assert response.status_code == 200

    data = response.json()

    assert data["mode"] == "dry_run"
    assert data["writes_performed"] is False
    assert data["embeddings_created"] is False
    assert "indexable_files" in data
    assert "excluded_file_count" in data
    assert "estimated_index_chunk_count" in data
    assert "exclusion_reason_counts" in data
    assert "files" in data
    assert "excluded_files" in data
