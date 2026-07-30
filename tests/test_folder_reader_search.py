from pathlib import Path

import pytest

from backend.app.folders.reader import ReadLimits, read_project_file
from backend.app.folders.safety import ProjectSafetyError
from backend.app.folders.search import search_project


def test_reader_reads_safe_text_with_relative_identity(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("def hello():\n    return 'world'\n", encoding="utf-8")

    result = read_project_file(tmp_path, "src/app.py")

    assert result["status"] == "readable"
    assert result["relative_path"] == "src/app.py"
    assert "return 'world'" in result["text"]
    assert result["bytes_read"] > 0


@pytest.mark.parametrize(
    ("relative", "content", "reason"),
    [
        (".env", "TOKEN=secret", "sensitive_file"),
        ("data.sqlite", "database", "blocked_file_type"),
        ("model.pt", "weights", "blocked_file_type"),
        ("file.png", "image", "unsupported_file_type"),
        ("report.csv:ZoNe.IdEnTiFiEr", "zone", "windows_download_metadata"),
    ],
)
def test_reader_skips_excluded_files(tmp_path: Path, relative: str, content: str, reason: str) -> None:
    (tmp_path / relative).write_text(content, encoding="utf-8")
    result = read_project_file(tmp_path, relative)
    assert result["status"] == "skipped"
    assert result["reason"] == reason
    assert result["text"] == ""


def test_reader_enforces_size_and_binary_limits(tmp_path: Path) -> None:
    (tmp_path / "large.txt").write_text("x" * 100, encoding="utf-8")
    (tmp_path / "binary.txt").write_bytes(b"safe-prefix\x00binary")
    limits = ReadLimits(max_file_size=50, max_bytes_per_file=50)
    assert read_project_file(tmp_path, "large.txt", limits=limits)["reason"] == "file_size_limit"
    assert read_project_file(tmp_path, "binary.txt")["reason"] == "binary_file"


def test_reader_does_not_expose_secret_values_in_ordinary_source_files(tmp_path: Path) -> None:
    (tmp_path / "config.py").write_text("API_KEY = 'sk-secretvalue123456'\n", encoding="utf-8")
    result = read_project_file(tmp_path, "config.py")
    assert result["status"] == "skipped"
    assert result["reason"] == "sensitive_content"
    assert result["text"] == ""


def test_reader_rejects_traversal_absolute_and_symlink_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.py"
    outside.write_text("secret = True", encoding="utf-8")
    with pytest.raises(ProjectSafetyError):
        read_project_file(tmp_path, "../outside.py")
    with pytest.raises(ProjectSafetyError):
        read_project_file(tmp_path, str(outside))
    link = tmp_path / "link.py"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(ProjectSafetyError):
        read_project_file(tmp_path, "link.py")


def test_search_ranks_exact_path_filename_and_content_deterministically(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "scanner.py").write_text("def scan_folder():\n    return 'inventory'\n", encoding="utf-8")
    (tmp_path / "tests" / "test_scanner.py").write_text("from src.scanner import scan_folder\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("Folder inventory architecture", encoding="utf-8")

    exact = search_project(tmp_path, "scanner", exact_path="src/scanner.py")
    filename = search_project(tmp_path, "test_scanner.py")
    content = search_project(tmp_path, "inventory architecture")

    assert exact["results"][0]["relative_path"] == "src/scanner.py"
    assert exact["results"][0]["match_reason"] == "exact_relative_path"
    assert filename["results"][0]["relative_path"] == "tests/test_scanner.py"
    assert content["results"][0]["relative_path"] == "README.md"
    assert content["results"][0]["excerpt"].startswith("1: ")
    assert all(not Path(item["relative_path"]).is_absolute() for item in content["results"])


def test_search_enforces_file_and_total_read_budgets(tmp_path: Path) -> None:
    for index in range(5):
        (tmp_path / f"file_{index}.txt").write_text("keyword " + "x" * 40, encoding="utf-8")
    result = search_project(
        tmp_path,
        "keyword",
        limits=ReadLimits(max_files=2, max_total_bytes=60, max_bytes_per_file=60),
    )
    assert result["inspected_files"] <= 2
    assert result["read_budget_exhausted"] is True


def test_search_prunes_dependency_and_git_directories(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / "node_modules").mkdir()
    (tmp_path / ".git" / "config").write_text("keyword", encoding="utf-8")
    (tmp_path / "node_modules" / "index.js").write_text("keyword", encoding="utf-8")
    result = search_project(tmp_path, "keyword")
    assert result["results"] == []
