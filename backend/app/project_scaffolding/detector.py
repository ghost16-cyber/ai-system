from __future__ import annotations

from pathlib import Path

from backend.app.repo_scanner.scanner import scan_repository

from .contracts import ScaffoldDetectionResult


def detect_scaffold_context(repository_root: str | Path) -> ScaffoldDetectionResult:
    root = Path(repository_root)
    if not root.exists() or not root.is_dir():
        return ScaffoldDetectionResult(
            frameworks=(), languages=(), structure_flags={}, suggested_category=None
        )
    repo_data = scan_repository(str(root), include_ast=False)
    frameworks = tuple(repo_data["frameworks"])
    languages = tuple(
        sorted(language for language in repo_data["languages"] if language != "unknown")
    )
    structure_flags = {
        key: value for key, value in repo_data["structure"].items() if isinstance(value, bool)
    }
    return ScaffoldDetectionResult(
        frameworks=frameworks,
        languages=languages,
        structure_flags=structure_flags,
        suggested_category=_suggest_category(frameworks, languages),
    )


def _suggest_category(frameworks: tuple[str, ...], languages: tuple[str, ...]) -> str | None:
    if "fastapi" in frameworks:
        return "fastapi_feature_module"
    if "react" in frameworks and "typescript" in languages:
        return "react_ts_feature_component"
    return None


__all__ = ["detect_scaffold_context"]
