from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from backend.app.project_scaffolding.contracts import (
    GeneratedFileManifest,
    GeneratedFileRecord,
    GeneratedFileSpec,
    OverwritePolicy,
    RequiredInputSpec,
    ScaffoldBlueprint,
    ScaffoldRenderResult,
)


def _file_spec(path: str = "pkg/${name}.py") -> GeneratedFileSpec:
    return GeneratedFileSpec(relative_path_template=path, content_template_ref="pkg/module.py.tmpl")


def test_blueprint_accepts_well_formed_definition() -> None:
    blueprint = ScaffoldBlueprint(
        blueprint_id="example",
        version=1,
        category="python_package",
        required_inputs=(RequiredInputSpec(name="name", description="pkg name"),),
        generated_files=(_file_spec(),),
    )
    assert blueprint.overwrite_policy == OverwritePolicy.REFUSE
    assert blueprint.version == 1


def test_blueprint_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        ScaffoldBlueprint(
            blueprint_id="example",
            version=1,
            category="python_package",
            generated_files=(_file_spec(),),
            unexpected_field="not allowed",
        )


def test_blueprint_rejects_duplicate_generated_file_paths() -> None:
    with pytest.raises(ValidationError):
        ScaffoldBlueprint(
            blueprint_id="example",
            version=1,
            category="python_package",
            generated_files=(_file_spec("pkg/a.py"), _file_spec("pkg/a.py")),
        )


def test_blueprint_rejects_duplicate_required_input_names() -> None:
    with pytest.raises(ValidationError):
        ScaffoldBlueprint(
            blueprint_id="example",
            version=1,
            category="python_package",
            required_inputs=(
                RequiredInputSpec(name="name", description="first"),
                RequiredInputSpec(name="name", description="second"),
            ),
            generated_files=(_file_spec(),),
        )


def test_blueprint_requires_at_least_one_generated_file() -> None:
    with pytest.raises(ValidationError):
        ScaffoldBlueprint(
            blueprint_id="example", version=1, category="python_package", generated_files=()
        )


def test_blueprint_rejects_invalid_overwrite_policy() -> None:
    with pytest.raises(ValidationError):
        ScaffoldBlueprint(
            blueprint_id="example",
            version=1,
            category="python_package",
            generated_files=(_file_spec(),),
            overwrite_policy="always",
        )


def _manifest() -> GeneratedFileManifest:
    return GeneratedFileManifest(
        blueprint_id="example",
        blueprint_version=1,
        template_hash="a" * 64,
        files=(
            GeneratedFileRecord(
                relative_path="pkg/a.py", content_hash="b" * 64, byte_size=10,
                content_template_ref="pkg/module.py.tmpl",
            ),
        ),
        total_byte_size=10,
        rendered_at=datetime.now(UTC),
    )


def test_render_result_requires_operations_to_match_manifest_file_count() -> None:
    with pytest.raises(ValidationError):
        ScaffoldRenderResult(
            blueprint_id="example",
            blueprint_version=1,
            manifest=_manifest(),
            operations=(),
        )


def test_render_result_accepts_matching_operations() -> None:
    result = ScaffoldRenderResult(
        blueprint_id="example",
        blueprint_version=1,
        manifest=_manifest(),
        operations=(
            {
                "relative_path": "pkg/a.py",
                "operation": "create",
                "new_content": "x",
                "result_sha256": "b" * 64,
            },
        ),
    )
    assert len(result.operations) == 1
