from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.rag.corpus_inventory import scan_corpus


def test_corpus_inventory_accepts_known_text_files(tmp_path: Path):
    (tmp_path / "main.py").write_text("print('hello')", encoding="utf-8")
    (tmp_path / "notes.md").write_text("# Notes", encoding="utf-8")
    (tmp_path / "data.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (tmp_path / "schema.sql").write_text("CREATE TABLE test(id INT);", encoding="utf-8")

    result = scan_corpus(tmp_path)

    accepted_paths = {
        item["relative_path"]
        for item in result["files"]
        if item["accepted"]
    }

    assert result["total_files"] == 4
    assert result["accepted_files"] == 4
    assert result["ignored_files"] == 0
    assert "main.py" in accepted_paths
    assert "notes.md" in accepted_paths
    assert "data.csv" in accepted_paths
    assert "schema.sql" in accepted_paths


def test_corpus_inventory_ignores_unsafe_files(tmp_path: Path):
    unsafe_files = [
        "data.crc",
        "dataset.parquet",
        "model.pt",
        "model.pth",
        "local.db",
        "local.sqlite",
        ".env",
        "shortcut.lnk",
        "image.png",
    ]

    for filename in unsafe_files:
        (tmp_path / filename).write_text("unsafe", encoding="utf-8")

    result = scan_corpus(tmp_path)

    assert result["total_files"] == len(unsafe_files)
    assert result["accepted_files"] == 0
    assert result["ignored_files"] == len(unsafe_files)

    for item in result["files"]:
        assert item["accepted"] is False


def test_corpus_inventory_prunes_ignored_folders(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('real file')", encoding="utf-8")

    (tmp_path / ".venv312" / "Lib" / "site-packages").mkdir(parents=True)
    (tmp_path / ".venv312" / "Lib" / "site-packages" / "noise.py").write_text(
        "print('ignore me')",
        encoding="utf-8",
    )

    (tmp_path / "node_modules" / "pkg").mkdir(parents=True)
    (tmp_path / "node_modules" / "pkg" / "index.js").write_text(
        "console.log('ignore me')",
        encoding="utf-8",
    )

    result = scan_corpus(tmp_path)

    paths = [item["relative_path"] for item in result["files"]]

    assert result["total_files"] == 1
    assert result["accepted_files"] == 1
    assert paths == ["src/app.py"]


def test_corpus_inventory_summary_and_chunk_estimate(tmp_path: Path):
    (tmp_path / "small.py").write_text("x" * 10, encoding="utf-8")
    (tmp_path / "large.md").write_text("x" * 4100, encoding="utf-8")
    (tmp_path / "ignored.crc").write_text("x" * 9999, encoding="utf-8")

    result = scan_corpus(tmp_path)

    assert result["total_files"] == 3
    assert result["accepted_files"] == 2
    assert result["ignored_files"] == 1
    assert result["file_type_counts"][".py"] == 1
    assert result["file_type_counts"][".md"] == 1
    assert result["file_type_counts"][".crc"] == 1
    assert result["accepted_type_counts"][".py"] == 1
    assert result["ignored_type_counts"][".crc"] == 1

    # small.py = 1 estimated chunk, large.md = 2 estimated chunks
    assert result["estimated_chunk_count"] == 3


def test_rag_corpus_inventory_endpoint():
    client = TestClient(app)

    response = client.get("/rag/corpus/inventory")

    assert response.status_code == 200

    data = response.json()

    assert "total_files" in data
    assert "accepted_files" in data
    assert "ignored_files" in data
    assert "file_type_counts" in data
    assert "estimated_chunk_count" in data
    assert "files" in data

def test_corpus_inventory_ignores_oversized_text_files(tmp_path: Path):
    (tmp_path / "small_notes.txt").write_text("small useful note", encoding="utf-8")
    (tmp_path / "large_dataset.csv").write_text("x" * 2_000_001, encoding="utf-8")

    result = scan_corpus(tmp_path)

    files = {item["relative_path"]: item for item in result["files"]}

    assert result["total_files"] == 2
    assert result["accepted_files"] == 1
    assert result["ignored_files"] == 1

    assert files["small_notes.txt"]["accepted"] is True
    assert files["large_dataset.csv"]["accepted"] is False
    assert files["large_dataset.csv"]["reason"] == "ignored_oversized_file"