from __future__ import annotations

import hashlib

import pytest

from backend.app.project_scaffolding.contracts import (
    GeneratedFileSpec,
    RequiredInputSpec,
    ScaffoldBlueprint,
)
from backend.app.project_scaffolding.registry import BLUEPRINT_REGISTRY
from backend.app.project_scaffolding.renderer import (
    InvalidScaffoldInputError,
    MissingRequiredScaffoldInputError,
    ScaffoldRenderError,
    render_blueprint,
)


def _python_package_blueprint() -> ScaffoldBlueprint:
    return BLUEPRINT_REGISTRY[("python_package", 1)]


def test_render_is_deterministic_across_repeated_calls() -> None:
    blueprint = _python_package_blueprint()
    inputs = {"package_name": "billing"}

    first = render_blueprint(blueprint, inputs)
    second = render_blueprint(blueprint, inputs)

    assert first.manifest.template_hash == second.manifest.template_hash
    assert first.manifest.files == second.manifest.files
    assert first.operations == second.operations


def test_render_produces_one_operation_per_generated_file() -> None:
    blueprint = _python_package_blueprint()
    result = render_blueprint(blueprint, {"package_name": "billing"})

    assert len(result.operations) == len(blueprint.generated_files)
    for operation in result.operations:
        assert operation["operation"] == "create"
        assert "billing" in operation["relative_path"]
        expected_hash = hashlib.sha256(operation["new_content"].encode("utf-8")).hexdigest()
        assert operation["result_sha256"] == expected_hash


def test_render_substitutes_path_and_content_templates() -> None:
    blueprint = _python_package_blueprint()
    result = render_blueprint(blueprint, {"package_name": "billing"})

    paths = {record.relative_path for record in result.manifest.files}
    assert "billing/__init__.py" in paths
    assert "billing/module.py" in paths


def test_missing_required_input_raises() -> None:
    blueprint = _python_package_blueprint()
    with pytest.raises(MissingRequiredScaffoldInputError):
        render_blueprint(blueprint, {})


def test_input_failing_validation_pattern_raises() -> None:
    blueprint = _python_package_blueprint()
    with pytest.raises(InvalidScaffoldInputError):
        render_blueprint(blueprint, {"package_name": "NotSnakeCase"})


def test_missing_default_input_is_skipped_when_not_required() -> None:
    blueprint = ScaffoldBlueprint(
        blueprint_id="optional_input_example",
        version=1,
        category="python_package",
        required_inputs=(
            RequiredInputSpec(name="package_name", description="pkg", required=True),
            RequiredInputSpec(
                name="unused_optional", description="not referenced", required=False
            ),
        ),
        generated_files=(
            GeneratedFileSpec(
                relative_path_template="${package_name}/__init__.py",
                content_template_ref="python_package/__init__.py.tmpl",
            ),
        ),
    )
    result = render_blueprint(blueprint, {"package_name": "billing"})
    assert result.manifest.files[0].relative_path == "billing/__init__.py"


def test_template_referencing_undeclared_input_raises() -> None:
    blueprint = ScaffoldBlueprint(
        blueprint_id="broken_example",
        version=1,
        category="python_package",
        required_inputs=(RequiredInputSpec(name="package_name", description="pkg"),),
        generated_files=(
            GeneratedFileSpec(
                relative_path_template="${package_name}/${undeclared}.py",
                content_template_ref="python_package/__init__.py.tmpl",
            ),
        ),
    )
    with pytest.raises(ScaffoldRenderError):
        render_blueprint(blueprint, {"package_name": "billing"})


def test_content_template_ref_must_exist() -> None:
    blueprint = ScaffoldBlueprint(
        blueprint_id="missing_template_example",
        version=1,
        category="python_package",
        required_inputs=(RequiredInputSpec(name="package_name", description="pkg"),),
        generated_files=(
            GeneratedFileSpec(
                relative_path_template="${package_name}/__init__.py",
                content_template_ref="python_package/does_not_exist.tmpl",
            ),
        ),
    )
    with pytest.raises(ScaffoldRenderError):
        render_blueprint(blueprint, {"package_name": "billing"})


def test_content_template_ref_cannot_escape_templates_directory() -> None:
    blueprint = ScaffoldBlueprint(
        blueprint_id="escaping_template_example",
        version=1,
        category="python_package",
        required_inputs=(RequiredInputSpec(name="package_name", description="pkg"),),
        generated_files=(
            GeneratedFileSpec(
                relative_path_template="${package_name}/__init__.py",
                content_template_ref="../../../../etc/passwd",
            ),
        ),
    )
    with pytest.raises(ScaffoldRenderError):
        render_blueprint(blueprint, {"package_name": "billing"})
