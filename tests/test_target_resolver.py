import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from repo_scanner.planner.target_resolver import resolve_target


def scan_for(paths):
    return {"files": [{"path": path} for path in paths]}


def test_resolve_exact_path():
    result = resolve_target(
        "src/models/base_model.py",
        scan_for(["src/models/base_model.py"]),
    )

    assert result.resolved_target == "src/models/base_model.py"
    assert result.reason == "exact_file_path"
    assert result.confidence == 1.0


def test_resolve_dotted_module_path():
    result = resolve_target(
        "analysis_engine.rules",
        scan_for(["analysis_engine/rules.py"]),
    )

    assert result.resolved_target == "analysis_engine/rules.py"
    assert result.reason == "exact_file_path"


def test_resolve_ambiguous_filename():
    result = resolve_target(
        "models.py",
        scan_for(["src/models.py", "vendor/requests/models.py"]),
    )

    assert result.resolved_target is None
    assert result.reason == "ambiguous_suffix_file_match"
    assert result.candidates == ["src/models.py", "vendor/requests/models.py"]


def test_resolve_ambiguous_filename_to_unique_directory_stem():
    result = resolve_target(
        "models.py",
        scan_for(
            [
                "src/models/__init__.py",
                "src/models/base_model.py",
                "vendor/requests/models.py",
            ]
        ),
    )

    assert result.resolved_target == "src/models"
    assert result.reason == "directory_from_file_stem_match"
    assert result.confidence == 0.72


def test_resolve_models_prefers_src_directory_over_data_and_vendor():
    result = resolve_target(
        "models.py",
        scan_for(
            [
                "data/models/pattern_clf.pkl",
                "src/models/__init__.py",
                "src/models/loader.py",
                "data/app/.venv/Lib/site-packages/pip/_internal/models/link.py",
                "data/app/.venv/Lib/site-packages/pip/_vendor/requests/models.py",
            ]
        ),
    )

    assert result.resolved_target == "src/models"
    assert result.reason == "directory_from_file_stem_match"


def test_resolve_directory_name():
    result = resolve_target(
        "models module",
        scan_for(["src/models/base_model.py", "src/models/registry.py"]),
    )

    assert result.resolved_target == "src/models"
    assert result.reason == "unique_directory_name_match"


def test_resolve_no_match():
    result = resolve_target("missing.py", scan_for(["src/main.py"]))

    assert result.resolved_target is None
    assert result.reason == "no_match"
