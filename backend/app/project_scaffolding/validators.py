from __future__ import annotations

import hashlib
import re

from backend.app.folders.safety import ProjectSafetyError, safe_relative_path

from .contracts import ScaffoldBlueprint, ScaffoldRenderResult
from .renderer import InvalidScaffoldInputError, MissingRequiredScaffoldInputError


IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
MAX_IDENTIFIER_LENGTH = 160
_UNRESOLVED_PLACEHOLDER_PATTERN = re.compile(
    r"\$\{[A-Za-z_][A-Za-z0-9_]*\}|\$[A-Za-z_][A-Za-z0-9_]*"
)


class ScaffoldValidationError(ValueError):
    """Base class for every Stage 2 deterministic scaffold validation failure."""


class InvalidBlueprintIdentifierError(ScaffoldValidationError):
    """A blueprint_id or category does not match the required identifier shape."""


class BlueprintNotFoundError(ScaffoldValidationError):
    """No registered blueprint matches the requested category/version."""


class DuplicateTemplateReferenceError(ScaffoldValidationError):
    """A blueprint reuses the same content_template_ref across generated files."""


class DestinationPathError(ScaffoldValidationError):
    """A rendered destination path fails traversal or repository-boundary checks."""


class ConflictingDestinationError(ScaffoldValidationError):
    """Two or more rendered files target the same destination path."""


class UnresolvedPlaceholderError(ScaffoldValidationError):
    """Rendered output still contains an unsubstituted template placeholder."""


class RenderIntegrityError(ScaffoldValidationError):
    """A render's declared hashes do not match its own content."""


def validate_identifier(value: str, *, label: str = "identifier") -> str:
    if not value or len(value) > MAX_IDENTIFIER_LENGTH or not IDENTIFIER_PATTERN.fullmatch(value):
        raise InvalidBlueprintIdentifierError(
            f"{label} {value!r} is not a valid deterministic scaffold identifier."
        )
    return value


def validate_no_duplicate_template_references(blueprint: ScaffoldBlueprint) -> None:
    seen: set[str] = set()
    for spec in blueprint.generated_files:
        if spec.content_template_ref in seen:
            raise DuplicateTemplateReferenceError(
                f"Blueprint {blueprint.blueprint_id!r} reuses template reference "
                f"{spec.content_template_ref!r} across multiple generated files."
            )
        seen.add(spec.content_template_ref)


def validate_required_inputs(blueprint: ScaffoldBlueprint, inputs: dict[str, str]) -> None:
    """Fail-closed pre-flight check mirroring the renderer's own input resolution,
    so missing/invalid inputs are rejected with a clear error before any template
    is rendered, rather than surfacing mid-render."""
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


def validate_destination_path(relative_path: str) -> str:
    """Reject path traversal and repository-boundary violations.

    Stage 2 has no real repository root to resolve against (no ProjectRun
    exists yet), so this is a purely logical check: no absolute paths, no
    drive letters, no '..' components. `safe_relative_path` already
    implements exactly this, and is the same guard the canonical project
    pipeline uses once a real root does exist -- reused rather than
    reimplemented.
    """
    try:
        return safe_relative_path(relative_path)
    except ProjectSafetyError as exc:
        raise DestinationPathError(str(exc)) from exc


def validate_no_conflicting_destinations(render_result: ScaffoldRenderResult) -> None:
    seen: set[str] = set()
    for record in render_result.manifest.files:
        if record.relative_path in seen:
            raise ConflictingDestinationError(
                f"Scaffold render targets {record.relative_path!r} more than once."
            )
        seen.add(record.relative_path)


def validate_no_unresolved_placeholders(render_result: ScaffoldRenderResult) -> None:
    for record in render_result.manifest.files:
        if _UNRESOLVED_PLACEHOLDER_PATTERN.search(record.relative_path):
            raise UnresolvedPlaceholderError(
                f"Destination path {record.relative_path!r} still contains an "
                "unresolved template placeholder."
            )
    for operation in render_result.operations:
        content = operation.get("new_content")
        if isinstance(content, str) and _UNRESOLVED_PLACEHOLDER_PATTERN.search(content):
            raise UnresolvedPlaceholderError(
                f"Rendered content for {operation.get('relative_path')!r} still "
                "contains an unresolved template placeholder."
            )


def validate_render_integrity(render_result: ScaffoldRenderResult) -> None:
    """Recompute every declared hash from the render's own content and confirm
    it matches -- an internal consistency check, not a re-render."""
    if len(render_result.operations) != len(render_result.manifest.files):
        raise RenderIntegrityError("Operation count does not match manifest file count.")
    operations_by_path = {
        str(operation.get("relative_path")): operation for operation in render_result.operations
    }
    for record in render_result.manifest.files:
        operation = operations_by_path.get(record.relative_path)
        if operation is None:
            raise RenderIntegrityError(
                f"Manifest entry {record.relative_path!r} has no corresponding render operation."
            )
        content = operation.get("new_content")
        declared_hash = operation.get("result_sha256")
        if not isinstance(content, str) or declared_hash != record.content_hash:
            raise RenderIntegrityError(
                f"Declared hash for {record.relative_path!r} does not match its manifest record."
            )
        if hashlib.sha256(content.encode("utf-8")).hexdigest() != record.content_hash:
            raise RenderIntegrityError(
                f"Recomputed hash for {record.relative_path!r} does not match the manifest."
            )
        if len(content.encode("utf-8")) != record.byte_size:
            raise RenderIntegrityError(
                f"Recomputed byte size for {record.relative_path!r} does not match the manifest."
            )


def validate_render_result(
    blueprint: ScaffoldBlueprint, render_result: ScaffoldRenderResult
) -> ScaffoldRenderResult:
    """Run every Stage 2 deterministic validation; return the render unchanged on success."""
    validate_identifier(blueprint.blueprint_id, label="blueprint_id")
    validate_identifier(blueprint.category, label="category")
    validate_no_duplicate_template_references(blueprint)
    for record in render_result.manifest.files:
        validate_destination_path(record.relative_path)
    validate_no_conflicting_destinations(render_result)
    validate_no_unresolved_placeholders(render_result)
    validate_render_integrity(render_result)
    return render_result


__all__ = [
    "ScaffoldValidationError",
    "InvalidBlueprintIdentifierError",
    "BlueprintNotFoundError",
    "DuplicateTemplateReferenceError",
    "DestinationPathError",
    "ConflictingDestinationError",
    "UnresolvedPlaceholderError",
    "RenderIntegrityError",
    "validate_identifier",
    "validate_no_duplicate_template_references",
    "validate_required_inputs",
    "validate_destination_path",
    "validate_no_conflicting_destinations",
    "validate_no_unresolved_placeholders",
    "validate_render_integrity",
    "validate_render_result",
]
