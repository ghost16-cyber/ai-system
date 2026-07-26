from __future__ import annotations

import itertools
import re
from pathlib import Path
from typing import Any

from .contracts import ScaffoldBlueprint
from .detector import detect_scaffold_context
from .registry import BLUEPRINT_REGISTRY, BlueprintRegistry
from .renderer import ScaffoldRenderError
from .validators import validate_required_inputs


_TEMPLATE_TOKEN = re.compile(r"\$\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


def derive_scaffold_hint(
    *,
    repository_root: str | Path,
    expected_files: list[str] | tuple[str, ...],
    registry: BlueprintRegistry | None = None,
) -> dict[str, Any] | None:
    """Deterministically derive a `scaffold_hint` for a work unit's planned
    files, or return None if no single registered blueprint's generated-file
    layout matches those files exactly.

    Matching is purely structural: each registered blueprint's own
    `generated_files` templates are turned into regexes and matched against
    `expected_files` as a whole -- every planned file must resolve against
    exactly one of the blueprint's templates, every template must be used
    exactly once, and every templated input value must be internally
    consistent and pass the blueprint's own `required_inputs` validation
    (e.g. `package_name`'s identifier pattern). No inference, no guessing,
    no partial matches -- if the planned file set doesn't correspond
    exactly to one registered blueprint, this returns None and the caller
    should keep relying on model synthesis, exactly as before this existed.
    """
    if not expected_files:
        return None
    normalized = sorted(dict.fromkeys(str(path).replace("\\", "/") for path in expected_files))
    detected = detect_scaffold_context(repository_root)
    source = BLUEPRINT_REGISTRY if registry is None else registry
    candidates = [
        blueprint for blueprint in source.values()
        if blueprint.framework is None or blueprint.framework in detected.frameworks
    ]
    for blueprint in candidates:
        inputs = _match_blueprint_templates(blueprint, normalized)
        if inputs is None:
            continue
        try:
            validate_required_inputs(blueprint, inputs)
        except ScaffoldRenderError:
            continue
        return {"category": blueprint.category, "inputs": inputs}
    return None


def _match_blueprint_templates(
    blueprint: ScaffoldBlueprint, paths: list[str]
) -> dict[str, str] | None:
    templates = [spec.relative_path_template for spec in blueprint.generated_files]
    if len(templates) != len(paths):
        return None
    patterns = [_template_to_pattern(template) for template in templates]
    for permutation in itertools.permutations(paths):
        resolved: dict[str, str] = {}
        matched = True
        for pattern, path in zip(patterns, permutation):
            match = pattern.match(path)
            if match is None:
                matched = False
                break
            for name, value in match.groupdict().items():
                if resolved.get(name, value) != value:
                    matched = False
                    break
                resolved[name] = value
            if not matched:
                break
        if matched:
            return resolved
    return None


def _template_to_pattern(template: str) -> re.Pattern[str]:
    pieces: list[str] = []
    cursor = 0
    for token in _TEMPLATE_TOKEN.finditer(template):
        pieces.append(re.escape(template[cursor:token.start()]))
        pieces.append(f"(?P<{token.group(1)}>[^/]+)")
        cursor = token.end()
    pieces.append(re.escape(template[cursor:]))
    return re.compile("^" + "".join(pieces) + "$")


__all__ = ["derive_scaffold_hint"]
