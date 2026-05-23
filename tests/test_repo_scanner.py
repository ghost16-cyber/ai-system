import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.repo_scanner import scan_repository


ROOT = Path(__file__).resolve().parents[1]


def test_scan_repository_extracts_python_metadata():
    scan = scan_repository(ROOT / "backend" / "app" / "repo_scanner")

    assert scan["summary"]["total_files"] >= 5
    assert scan["languages"]["python"] >= 5
    assert scan["file_types"][".py"] >= 5
    assert ".pyc" not in scan["file_types"]
    assert scan["python"]["files_parsed"] >= 5
    assert {"name": "scan_repository", "path": "scanner.py"} in scan["python"]["functions"]
    assert "pathlib" in scan["python"]["imports"]
