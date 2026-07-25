from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from pathlib import Path
from string import Template

from backend.app.project_control.contracts import content_hash

from .contracts import (
    GeneratedFileManifest,
    GeneratedFileRecord,
    ScaffoldBlueprint,
    ScaffoldRenderResult,
)


TEMPLATES_ROOT = Path(__file__).resolve().parent / "templates"


class ScaffoldRenderError(ValueError):
    """The blueprint could not be rendered from the supplied inputs."""


class MissingRequiredScaffoldInputError(ScaffoldRenderError):
    """A required blueprint input was not supplied and has no default."""


class InvalidScaffoldInputError(ScaffoldRenderError):
    """A supplied blueprint input failed its validation pattern."""


def render_blueprint(
    blueprint: ScaffoldBlueprint, inputs: dict[str, str]
) -> ScaffoldRenderResult:
    values = _resolve_inputs(blueprint, inputs)

    file_records: list[GeneratedFileRecord] = []
    operations: list[dict[str, object]] = []
    total_byte_size = 0

    for spec in blueprint.generated_files:
        relative_path = _substitute(spec.relative_path_template, values, context="path")
        template_text = _load_template_text(spec.content_template_ref)
        content = _substitute(template_text, values, context="content")
        content_bytes = content.encode("utf-8")
        file_hash = hashlib.sha256(content_bytes).hexdigest()
        byte_size = len(content_bytes)
        total_byte_size += byte_size

        file_records.append(
            GeneratedFileRecord(
                relative_path=relative_path, content_hash=file_hash, byte_size=byte_size
            )
        )
        operations.append(
            {
                "relative_path": relative_path,
                "operation": "create",
                "new_content": content,
                "result_sha256": file_hash,
            }
        )

    template_hash = content_hash(
        {
            "blueprint_id": blueprint.blueprint_id,
            "blueprint_version": blueprint.version,
            "files": [
                {"relative_path": record.relative_path, "content_hash": record.content_hash}
                for record in file_records
            ],
        }
    )

    manifest = GeneratedFileManifest(
        blueprint_id=blueprint.blueprint_id,
        blueprint_version=blueprint.version,
        template_hash=template_hash,
        files=tuple(file_records),
        total_byte_size=total_byte_size,
        rendered_at=datetime.now(UTC),
    )
    return ScaffoldRenderResult(
        blueprint_id=blueprint.blueprint_id,
        blueprint_version=blueprint.version,
        manifest=manifest,
        operations=tuple(operations),
    )


def _resolve_inputs(blueprint: ScaffoldBlueprint, inputs: dict[str, str]) -> dict[str, str]:
    resolved: dict[str, str] = {}
    for spec in blueprint.required_inputs:
        value = inputs.get(spec.name, spec.default)
        if value is None:
            if spec.required:
                raise MissingRequiredScaffoldInputError(
                    f"Required scaffold input {spec.name!r} was not supplied."
                )
            continue
        if spec.validation_pattern is not None and not re.fullmatch(
            spec.validation_pattern, value
        ):
            raise InvalidScaffoldInputError(
                f"Scaffold input {spec.name!r} value {value!r} does not match the "
                f"required pattern {spec.validation_pattern!r}."
            )
        resolved[spec.name] = value
    return resolved


def _substitute(template_text: str, values: dict[str, str], *, context: str) -> str:
    try:
        return Template(template_text).substitute(values)
    except KeyError as exc:
        raise ScaffoldRenderError(
            f"Blueprint {context} template references undeclared input {exc}."
        ) from exc


def _load_template_text(content_template_ref: str) -> str:
    candidate = (TEMPLATES_ROOT / content_template_ref).resolve()
    if TEMPLATES_ROOT != candidate and TEMPLATES_ROOT not in candidate.parents:
        raise ScaffoldRenderError(
            f"Template reference {content_template_ref!r} escapes the templates directory."
        )
    if not candidate.is_file():
        raise ScaffoldRenderError(f"Template reference {content_template_ref!r} does not exist.")
    return candidate.read_text(encoding="utf-8")


__all__ = [
    "TEMPLATES_ROOT",
    "ScaffoldRenderError",
    "MissingRequiredScaffoldInputError",
    "InvalidScaffoldInputError",
    "render_blueprint",
]
