from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest

from backend.app.project_scaffolding.contracts import (
    GeneratedFileManifest,
    GeneratedFileRecord,
    GeneratedFileSpec,
    RequiredInputSpec,
    ScaffoldBlueprint,
    ScaffoldRenderResult,
)
from backend.app.project_scaffolding.renderer import (
    InvalidScaffoldInputError,
    MissingRequiredScaffoldInputError,
)
from backend.app.project_scaffolding.validators import (
    ConflictingDestinationError,
    DestinationPathError,
    DuplicateTemplateReferenceError,
    InvalidBlueprintIdentifierError,
    RenderIntegrityError,
    UnresolvedPlaceholderError,
    validate_destination_path,
    validate_identifier,
    validate_no_conflicting_destinations,
    validate_no_duplicate_template_references,
    validate_no_unresolved_placeholders,
    validate_render_integrity,
    validate_required_inputs,
)


def _blueprint(**overrides) -> ScaffoldBlueprint:
    values = dict(
        blueprint_id="example_blueprint",
        version=1,
        category="python_package",
        generated_files=(
            GeneratedFileSpec(
                relative_path_template="pkg/a.py", content_template_ref="python_package/module.py.tmpl"
            ),
        ),
    )
    values.update(overrides)
    return ScaffoldBlueprint(**values)


def _render_result(*, paths_and_contents: tuple[tuple[str, str], ...]) -> ScaffoldRenderResult:
    records = []
    operations = []
    for path, content in paths_and_contents:
        file_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        records.append(
            GeneratedFileRecord(
                relative_path=path, content_hash=file_hash,
                byte_size=len(content.encode("utf-8")),
                content_template_ref="python_package/module.py.tmpl",
            )
        )
        operations.append(
            {
                "relative_path": path,
                "operation": "create",
                "new_content": content,
                "result_sha256": file_hash,
            }
        )
    manifest = GeneratedFileManifest(
        blueprint_id="example_blueprint",
        blueprint_version=1,
        template_hash="a" * 64,
        files=tuple(records),
        total_byte_size=sum(len(c.encode("utf-8")) for _, c in paths_and_contents),
        rendered_at=datetime.now(UTC),
    )
    return ScaffoldRenderResult(
        blueprint_id="example_blueprint", blueprint_version=1,
        manifest=manifest, operations=tuple(operations),
    )


def test_validate_identifier_accepts_snake_case() -> None:
    assert validate_identifier("fastapi_feature_module") == "fastapi_feature_module"


@pytest.mark.parametrize(
    "value", ["", "Invalid-ID", "1starts_with_digit", "has space", "UPPER", "a" * 161]
)
def test_validate_identifier_rejects_invalid_forms(value: str) -> None:
    with pytest.raises(InvalidBlueprintIdentifierError):
        validate_identifier(value)


def test_duplicate_template_reference_is_rejected() -> None:
    blueprint = _blueprint(
        generated_files=(
            GeneratedFileSpec(
                relative_path_template="pkg/a.py", content_template_ref="python_package/module.py.tmpl"
            ),
            GeneratedFileSpec(
                relative_path_template="pkg/b.py", content_template_ref="python_package/module.py.tmpl"
            ),
        )
    )
    with pytest.raises(DuplicateTemplateReferenceError):
        validate_no_duplicate_template_references(blueprint)


def test_distinct_template_references_are_accepted() -> None:
    blueprint = _blueprint(
        generated_files=(
            GeneratedFileSpec(
                relative_path_template="pkg/a.py", content_template_ref="python_package/module.py.tmpl"
            ),
            GeneratedFileSpec(
                relative_path_template="pkg/__init__.py",
                content_template_ref="python_package/__init__.py.tmpl",
            ),
        )
    )
    validate_no_duplicate_template_references(blueprint)


@pytest.mark.parametrize(
    "path", ["../secret.py", "/etc/passwd", "C:/secret.py", "pkg/../../secret.py"]
)
def test_destination_path_traversal_is_rejected(path: str) -> None:
    with pytest.raises(DestinationPathError):
        validate_destination_path(path)


def test_safe_destination_path_is_normalized() -> None:
    assert validate_destination_path("pkg/./a.py") == "pkg/a.py"


def test_conflicting_destinations_are_rejected() -> None:
    render_result = _render_result(paths_and_contents=(("pkg/a.py", "x"), ("pkg/a.py", "y")))
    with pytest.raises(ConflictingDestinationError):
        validate_no_conflicting_destinations(render_result)


def test_distinct_destinations_are_accepted() -> None:
    render_result = _render_result(paths_and_contents=(("pkg/a.py", "x"), ("pkg/b.py", "y")))
    validate_no_conflicting_destinations(render_result)


def test_unresolved_placeholder_in_content_is_rejected() -> None:
    render_result = _render_result(
        paths_and_contents=(("pkg/a.py", "value = '${leftover_variable}'"),)
    )
    with pytest.raises(UnresolvedPlaceholderError):
        validate_no_unresolved_placeholders(render_result)


def test_unresolved_placeholder_in_path_is_rejected() -> None:
    render_result = _render_result(paths_and_contents=(("pkg/${leftover}.py", "value = 1"),))
    with pytest.raises(UnresolvedPlaceholderError):
        validate_no_unresolved_placeholders(render_result)


def test_fully_resolved_content_has_no_unresolved_placeholders() -> None:
    render_result = _render_result(paths_and_contents=(("pkg/a.py", "value = 1"),))
    validate_no_unresolved_placeholders(render_result)


def test_literal_dollar_signs_are_not_mistaken_for_placeholders() -> None:
    render_result = _render_result(paths_and_contents=(("pkg/a.py", "price = '$$5.00'"),))
    validate_no_unresolved_placeholders(render_result)


def test_render_integrity_accepts_matching_hashes() -> None:
    render_result = _render_result(paths_and_contents=(("pkg/a.py", "value = 1"),))
    validate_render_integrity(render_result)


def test_render_integrity_rejects_tampered_hash() -> None:
    render_result = _render_result(paths_and_contents=(("pkg/a.py", "value = 1"),))
    tampered_manifest = render_result.manifest.model_copy(
        update={
            "files": (
                render_result.manifest.files[0].model_copy(update={"content_hash": "f" * 64}),
            )
        }
    )
    tampered = render_result.model_copy(update={"manifest": tampered_manifest})
    with pytest.raises(RenderIntegrityError):
        validate_render_integrity(tampered)


def test_render_integrity_rejects_missing_operation_for_manifest_entry() -> None:
    render_result = _render_result(paths_and_contents=(("pkg/a.py", "value = 1"),))
    detached = render_result.model_copy(update={"operations": ()})
    with pytest.raises(RenderIntegrityError):
        validate_render_integrity(detached)


def test_required_input_missing_is_rejected() -> None:
    blueprint = _blueprint(
        required_inputs=(RequiredInputSpec(name="package_name", description="pkg"),)
    )
    with pytest.raises(MissingRequiredScaffoldInputError):
        validate_required_inputs(blueprint, {})


def test_required_input_invalid_pattern_is_rejected() -> None:
    blueprint = _blueprint(
        required_inputs=(
            RequiredInputSpec(
                name="package_name", description="pkg", validation_pattern=r"^[a-z]+$"
            ),
        )
    )
    with pytest.raises(InvalidScaffoldInputError):
        validate_required_inputs(blueprint, {"package_name": "NOT-VALID"})
