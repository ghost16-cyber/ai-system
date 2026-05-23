import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.repo_scanner.planner.target_resolver import resolve_target


def scan_for(paths):
    return {"files": [{"path": path} for path in paths]}


def test_resolve_exact_path():
    result = resolve_target(
        "backend/app/llm/loader.py",
        scan_for(["backend/app/llm/loader.py"]),
    )

    assert result.resolved_target == "backend/app/llm/loader.py"
    assert result.reason == "exact_file_path"
    assert result.confidence == 1.0
    assert result.target_kind == "file"


def test_resolve_dotted_module_path():
    result = resolve_target(
        "analysis_engine.rules",
        scan_for(["analysis_engine/rules.py"]),
    )

    assert result.resolved_target == "analysis_engine/rules.py"
    assert result.reason == "exact_file_path"
    assert result.target_kind == "file"


def test_resolve_ambiguous_filename():
    result = resolve_target(
        "llm.py",
        scan_for(["backend/app/llm.py", "vendor/requests/llm.py"]),
    )

    assert result.resolved_target is None
    assert result.reason == "ambiguous_suffix_file_match"
    assert result.candidates == ["backend/app/llm.py", "vendor/requests/llm.py"]
    assert result.target_kind == "unknown"


def test_resolve_ambiguous_filename_to_unique_directory_stem():
    result = resolve_target(
        "llm.py",
        scan_for(
            [
                "backend/app/llm/__init__.py",
                "backend/app/llm/loader.py",
                "vendor/requests/llm.py",
            ]
        ),
    )

    assert result.resolved_target == "backend/app/llm"
    assert result.reason == "directory_from_file_stem_match"
    assert result.confidence == 0.72
    assert result.target_kind == "directory"


def test_resolve_llm_prefers_app_directory_over_data_and_vendor():
    result = resolve_target(
        "llm.py",
        scan_for(
            [
                "data/models/pattern_clf.pkl",
                "backend/app/llm/__init__.py",
                "backend/app/llm/loader.py",
                "data/app/.venv/Lib/site-packages/pip/_internal/llm/link.py",
                "data/app/.venv/Lib/site-packages/pip/_vendor/requests/llm.py",
            ]
        ),
    )

    assert result.resolved_target == "backend/app/llm"
    assert result.reason == "directory_from_file_stem_match"
    assert result.target_kind == "directory"


def test_resolve_directory_name():
    result = resolve_target(
        "llm module",
        scan_for(["backend/app/llm/loader.py", "backend/app/llm/quantizer.py"]),
    )

    assert result.resolved_target == "backend/app/llm"
    assert result.reason == "unique_directory_name_match"
    assert result.target_kind == "directory"


def test_resolve_no_match():
    result = resolve_target("missing.py", scan_for(["backend/app/main.py"]))

    assert result.resolved_target is None
    assert result.reason == "no_match"
    assert result.target_kind == "unknown"
