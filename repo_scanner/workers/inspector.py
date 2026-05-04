# repo_scanner/workers/inspector.py

from __future__ import annotations

from pathlib import Path

from repo_scanner.ast_parser import parse_python_file
from repo_scanner.workers.inspect_schema import (
    InspectResult,
    FileSummary,
    DirectorySummary,
)


def inspect_target(base_path: Path, target: str, target_kind: str) -> InspectResult:
    """
    Inspect a repository target (file, directory, or module) and return a
    structured result.

    For files we now also compute:
      * ``complexity_score`` – a very cheap heuristic based on the number of
        functions and classes.
      * ``role_hint`` – a high‑level guess of the file’s role (model, API,
        training logic, etc.) using ``infer_role``.
    """
    full_path = (base_path / target).resolve()

    if not full_path.exists():
        return InspectResult(
            target=target,
            target_kind="unknown",
            summary="Target does not exist in repository.",
        )

    # -----------------------------------------------------------------
    # File inspection – parse the Python source and enrich the summary
    # -----------------------------------------------------------------
    if target_kind == "file" and full_path.is_file():
        try:
            parsed = parse_python_file(full_path)

            functions = parsed.get("functions", [])
            classes = parsed.get("classes", [])
            imports = parsed.get("imports", [])

            # ---- NEW: compute role and complexity -----------------------
            role = infer_role(target, functions, classes)
            # Simple heuristic: each function = 1 point, each class = 2 points
            complexity = len(functions) + len(classes) * 2

            file_summary = FileSummary(
                path=target,
                functions=functions,
                classes=classes,
                imports=imports,
                complexity_score=complexity,
                role_hint=role,
            )

            return InspectResult(
                target=target,
                target_kind="file",
                summary="Python file parsed successfully.",
                file_summary=file_summary,
            )

        except Exception as e:
            return InspectResult(
                target=target,
                target_kind="file",
                summary=f"Failed to parse file: {e}",
            )

    # -----------------------------------------------------------------
    # Directory (or module) inspection – list immediate children
    # -----------------------------------------------------------------
    if target_kind in {"directory", "module"} and full_path.is_dir():
        files: list[str] = []
        subdirs: list[str] = []

        for item in full_path.iterdir():
            if item.is_file():
                files.append(item.name)
            elif item.is_dir():
                subdirs.append(item.name)

        return InspectResult(
            target=target,
            target_kind="directory",
            summary="Directory structure extracted.",
            directory_summary=DirectorySummary(
                path=target,
                files=files,
                subdirectories=subdirs,
            ),
        )

    # -----------------------------------------------------------------
    # Fallback for unsupported kinds
    # -----------------------------------------------------------------
    return InspectResult(
        target=target,
        target_kind="unknown",
        summary="Unsupported target type.",
    )


def infer_role(path: str, functions: list[str], classes: list[str]) -> str:
    """
    Very lightweight heuristic to guess the “role” of a Python file.
    Used only for the optional ``role_hint`` field in ``FileSummary``.
    """
    p = path.lower()

    if "model" in p:
        return "data_model"
    if "route" in p or "api" in p:
        return "api_layer"
    if "train" in p:
        return "training_logic"
    if "test" in p:
        return "test_code"
    if len(classes) > 0:
        return "core_logic"

    return "utility"