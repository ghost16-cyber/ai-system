from __future__ import annotations

import pytest

from backend.app.project_scaffolding.contracts import (
    GeneratedFileSpec,
    ScaffoldBlueprint,
    ScaffoldDetectionResult,
)
from backend.app.project_scaffolding.registry import (
    ScaffoldRegistryError,
    register_blueprint,
    select_blueprint,
)


def _blueprint(blueprint_id: str, version: int, category: str, framework: str | None) -> ScaffoldBlueprint:
    return ScaffoldBlueprint(
        blueprint_id=blueprint_id,
        version=version,
        category=category,
        framework=framework,
        generated_files=(
            GeneratedFileSpec(
                relative_path_template=f"{blueprint_id}/file.py",
                content_template_ref="python_package/module.py.tmpl",
            ),
        ),
    )


def test_register_and_select_exact_framework_match() -> None:
    registry: dict = {}
    fastapi_blueprint = _blueprint("fastapi_mod", 1, "feature_module", "fastapi")
    register_blueprint(fastapi_blueprint, registry=registry)

    detected = ScaffoldDetectionResult(frameworks=("fastapi",), languages=("python",))
    selected = select_blueprint(detected, "feature_module", registry=registry)
    assert selected is fastapi_blueprint


def test_select_returns_none_when_framework_does_not_match_and_no_agnostic_blueprint() -> None:
    registry: dict = {}
    register_blueprint(_blueprint("fastapi_mod", 1, "feature_module", "fastapi"), registry=registry)

    detected = ScaffoldDetectionResult(frameworks=("django",), languages=("python",))
    assert select_blueprint(detected, "feature_module", registry=registry) is None


def test_select_returns_none_for_unknown_category() -> None:
    registry: dict = {}
    register_blueprint(_blueprint("fastapi_mod", 1, "feature_module", "fastapi"), registry=registry)

    detected = ScaffoldDetectionResult(frameworks=("fastapi",), languages=("python",))
    assert select_blueprint(detected, "does_not_exist", registry=registry) is None


def test_select_falls_back_to_framework_agnostic_blueprint() -> None:
    registry: dict = {}
    agnostic = _blueprint("generic_mod", 1, "feature_module", None)
    register_blueprint(agnostic, registry=registry)

    detected = ScaffoldDetectionResult(frameworks=(), languages=("python",))
    assert select_blueprint(detected, "feature_module", registry=registry) is agnostic


def test_select_prefers_highest_matching_version() -> None:
    registry: dict = {}
    v1 = _blueprint("mod", 1, "feature_module", None)
    v2 = _blueprint("mod", 2, "feature_module", None)
    register_blueprint(v1, registry=registry)
    register_blueprint(v2, registry=registry)

    detected = ScaffoldDetectionResult()
    assert select_blueprint(detected, "feature_module", registry=registry) is v2


def test_select_honors_explicit_requested_version() -> None:
    registry: dict = {}
    v1 = _blueprint("mod", 1, "feature_module", None)
    v2 = _blueprint("mod", 2, "feature_module", None)
    register_blueprint(v1, registry=registry)
    register_blueprint(v2, registry=registry)

    detected = ScaffoldDetectionResult()
    assert select_blueprint(detected, "feature_module", requested_version=1, registry=registry) is v1


def test_duplicate_registration_is_rejected() -> None:
    registry: dict = {}
    register_blueprint(_blueprint("mod", 1, "feature_module", None), registry=registry)
    with pytest.raises(ScaffoldRegistryError):
        register_blueprint(_blueprint("mod", 1, "feature_module", None), registry=registry)


def test_default_registry_has_five_initial_categories() -> None:
    from backend.app.project_scaffolding.registry import BLUEPRINT_REGISTRY

    categories = {blueprint.category for blueprint in BLUEPRINT_REGISTRY.values()}
    assert categories == {
        "fastapi_feature_module",
        "react_ts_feature_component",
        "python_package",
        "pytest_test_module",
        "route_service_repository_module",
    }
