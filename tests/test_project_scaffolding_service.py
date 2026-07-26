from __future__ import annotations

import pytest

from backend.app.project_scaffolding.contracts import (
    GeneratedFileSpec,
    RequiredInputSpec,
    ScaffoldBlueprint,
    ScaffoldDetectionResult,
    ScaffoldRenderResult,
)
from backend.app.project_scaffolding.registry import BLUEPRINT_REGISTRY, register_blueprint
from backend.app.project_scaffolding.service import ProjectScaffoldingService
from backend.app.project_scaffolding.validators import (
    BlueprintNotFoundError,
    ConflictingDestinationError,
    DestinationPathError,
    DuplicateTemplateReferenceError,
    InvalidBlueprintIdentifierError,
)


def _python_package_blueprint() -> ScaffoldBlueprint:
    return BLUEPRINT_REGISTRY[("python_package", 1)]


def test_successful_deterministic_render_produces_immutable_result() -> None:
    service = ProjectScaffoldingService()
    render = service.render_blueprint(_python_package_blueprint(), {"package_name": "billing"})

    assert isinstance(render, ScaffoldRenderResult)
    assert render.manifest.blueprint_id == "python_package"
    assert render.manifest.blueprint_version == 1
    paths = {entry.relative_path for entry in render.manifest.files}
    assert paths == {"billing/__init__.py", "billing/module.py"}
    for entry in render.manifest.files:
        assert entry.content_template_ref.startswith("python_package/")


def test_render_via_category_lookup_matches_direct_blueprint_render() -> None:
    service = ProjectScaffoldingService()
    by_category = service.render(category="python_package", inputs={"package_name": "billing"})
    by_blueprint = service.render_blueprint(
        _python_package_blueprint(), {"package_name": "billing"}
    )
    assert by_category.manifest.template_hash == by_blueprint.manifest.template_hash


def test_unknown_category_raises_blueprint_not_found() -> None:
    service = ProjectScaffoldingService()
    with pytest.raises(BlueprintNotFoundError):
        service.render(category="does_not_exist", inputs={})


def test_invalid_blueprint_identifier_is_rejected() -> None:
    blueprint = ScaffoldBlueprint(
        blueprint_id="Not-A-Valid-Id",
        version=1,
        category="python_package",
        generated_files=(
            GeneratedFileSpec(
                relative_path_template="pkg/__init__.py",
                content_template_ref="python_package/__init__.py.tmpl",
            ),
        ),
    )
    service = ProjectScaffoldingService()
    with pytest.raises(InvalidBlueprintIdentifierError):
        service.render_blueprint(blueprint, {})


def test_duplicate_resolved_destination_is_rejected() -> None:
    # Two distinct templates that resolve to the identical final path once
    # substituted -- Stage 1's blueprint-level check only rejects duplicate
    # *template strings*, not duplicate *resolved* paths, so this is exactly
    # the gap Stage 2 closes.
    blueprint = ScaffoldBlueprint(
        blueprint_id="duplicate_destination_example",
        version=1,
        category="python_package",
        required_inputs=(RequiredInputSpec(name="package_name", description="pkg"),),
        generated_files=(
            GeneratedFileSpec(
                relative_path_template="${package_name}/module.py",
                content_template_ref="python_package/module.py.tmpl",
            ),
            GeneratedFileSpec(
                relative_path_template="billing/module.py",
                content_template_ref="python_package/__init__.py.tmpl",
            ),
        ),
    )
    service = ProjectScaffoldingService()
    with pytest.raises(ConflictingDestinationError):
        service.render_blueprint(blueprint, {"package_name": "billing"})


def test_path_traversal_via_unvalidated_input_is_rejected() -> None:
    # No validation_pattern is declared for this input, so nothing upstream
    # of Stage 2 stops a traversal attempt from reaching the destination path.
    blueprint = ScaffoldBlueprint(
        blueprint_id="traversal_example",
        version=1,
        category="python_package",
        required_inputs=(RequiredInputSpec(name="package_name", description="pkg"),),
        generated_files=(
            GeneratedFileSpec(
                relative_path_template="${package_name}/module.py",
                content_template_ref="python_package/module.py.tmpl",
            ),
        ),
    )
    service = ProjectScaffoldingService()
    with pytest.raises(DestinationPathError):
        service.render_blueprint(blueprint, {"package_name": "../../etc/passwd"})


def test_duplicate_template_reference_is_rejected_at_service_level() -> None:
    blueprint = ScaffoldBlueprint(
        blueprint_id="duplicate_template_example",
        version=1,
        category="python_package",
        generated_files=(
            GeneratedFileSpec(
                relative_path_template="pkg/a.py", content_template_ref="python_package/module.py.tmpl"
            ),
            GeneratedFileSpec(
                relative_path_template="pkg/b.py", content_template_ref="python_package/module.py.tmpl"
            ),
        ),
    )
    service = ProjectScaffoldingService()
    with pytest.raises(DuplicateTemplateReferenceError):
        service.render_blueprint(blueprint, {})


def test_identical_input_produces_identical_manifest_hash() -> None:
    service = ProjectScaffoldingService()
    first = service.render_blueprint(_python_package_blueprint(), {"package_name": "billing"})
    second = service.render_blueprint(_python_package_blueprint(), {"package_name": "billing"})

    assert first.manifest.template_hash == second.manifest.template_hash
    assert first.manifest.files == second.manifest.files
    assert first.operations == second.operations


def test_differing_input_produces_differing_manifest_hash() -> None:
    service = ProjectScaffoldingService()
    first = service.render_blueprint(_python_package_blueprint(), {"package_name": "billing"})
    second = service.render_blueprint(_python_package_blueprint(), {"package_name": "invoicing"})

    assert first.manifest.template_hash != second.manifest.template_hash


def test_manifest_hash_excludes_the_nondeterministic_generation_timestamp() -> None:
    service = ProjectScaffoldingService()
    first = service.render_blueprint(_python_package_blueprint(), {"package_name": "billing"})
    second = service.render_blueprint(_python_package_blueprint(), {"package_name": "billing"})

    # Same blueprint+inputs, rendered independently -- the manifest_hash must
    # match even though each render computed its own `generated_at` timestamp.
    assert first.manifest.template_hash == second.manifest.template_hash


def test_isolated_registry_does_not_leak_into_the_default_registry() -> None:
    isolated: dict = {}
    blueprint = ScaffoldBlueprint(
        blueprint_id="isolated_example",
        version=1,
        category="isolated_category",
        required_inputs=(RequiredInputSpec(name="package_name", description="pkg"),),
        generated_files=(
            GeneratedFileSpec(
                relative_path_template="pkg/__init__.py",
                content_template_ref="python_package/__init__.py.tmpl",
            ),
        ),
    )
    register_blueprint(blueprint, registry=isolated)
    service = ProjectScaffoldingService(registry=isolated)

    render = service.render(
        category="isolated_category", inputs={"package_name": "isolated"}
    )
    assert render.manifest.blueprint_id == "isolated_example"

    default_service = ProjectScaffoldingService()
    with pytest.raises(BlueprintNotFoundError):
        default_service.render(category="isolated_category", inputs={})


def test_render_accepts_a_precomputed_detection_result_without_touching_the_filesystem() -> None:
    service = ProjectScaffoldingService()
    render = service.render(
        category="python_package",
        inputs={"package_name": "billing"},
        detected=ScaffoldDetectionResult(),
    )
    assert render.manifest.blueprint_id == "python_package"
