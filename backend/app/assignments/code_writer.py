from __future__ import annotations

import re
from pathlib import Path

from backend.app.core.path_utils import normalize_path_for_platform
from backend.app.assignments.schemas import (
    AssignmentCodeBlueprint,
    AssignmentCodeBlueprintSet,
    AssignmentCodeWriteResult,
)


def write_code_blueprints(
    workspace_root: str | Path,
    blueprint_sets: AssignmentCodeBlueprintSet | list[AssignmentCodeBlueprintSet],
    *,
    overwrite: bool = False,
) -> AssignmentCodeWriteResult:
    root = normalize_path_for_platform(workspace_root).path.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    sets = blueprint_sets if isinstance(blueprint_sets, list) else [blueprint_sets]
    created: list[str] = []
    skipped: list[str] = []
    refused: list[str] = []
    warnings: list[str] = []

    for blueprint in _flatten_blueprints(sets):
        try:
            target = _safe_target(root, blueprint.file_path)
        except ValueError:
            refused.append(blueprint.file_path)
            continue
        if _contains_real_credential(blueprint.generated_content):
            refused.append(blueprint.file_path)
            warnings.append(f"Refused {blueprint.file_path}: content appears to contain a real credential.")
            continue
        relative = target.relative_to(root).as_posix()
        if target.exists() and not overwrite:
            skipped.append(relative)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(blueprint.generated_content, encoding="utf-8")
        created.append(relative)

    next_manual_steps = [
        "Review generated files before running anything.",
        "Replace placeholder paths and environment variables with verified local values.",
        "Run commands manually only after reviewing the runbook.",
        "Capture screenshots and evidence from actual outputs.",
    ]
    return AssignmentCodeWriteResult(
        workspace_path=str(root),
        created_files=created,
        skipped_files=skipped,
        refused_files=refused,
        warnings=warnings,
        next_manual_steps=next_manual_steps,
        overwrite=overwrite,
        commands_executed=False,
        credentials_written=False,
    )


def _flatten_blueprints(blueprint_sets: list[AssignmentCodeBlueprintSet]) -> list[AssignmentCodeBlueprint]:
    result: list[AssignmentCodeBlueprint] = []
    for blueprint_set in blueprint_sets:
        result.extend(blueprint_set.blueprints)
    return result


def _safe_target(root: Path, relative_path: str) -> Path:
    path = Path(relative_path)
    if path.is_absolute() or any(part == ".." for part in path.parts):
        raise ValueError("Blueprint file path must stay inside the workspace.")
    target = (root / path).resolve()
    try:
        target.relative_to(root)
    except ValueError as error:
        raise ValueError("Blueprint file path must stay inside the workspace.") from error
    return target


def _contains_real_credential(content: str) -> bool:
    for match in re.finditer(r"(?i)(password|secret|token|api[_-]?key)\s*[:=]\s*[\"']([^\"']+)[\"']", content):
        value = match.group(2).strip()
        if value and not _is_placeholder_secret(value):
            return True
    return False


def _is_placeholder_secret(value: str) -> bool:
    lowered = value.lower()
    return (
        lowered.startswith("replace")
        or "placeholder" in lowered
        or "example" in lowered
        or "local" in lowered
        or lowered in {"", "none", "changeme"}
    )
