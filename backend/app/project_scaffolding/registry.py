from __future__ import annotations

from .contracts import (
    GeneratedFileSpec,
    RequiredInputSpec,
    ScaffoldBlueprint,
    ScaffoldDetectionResult,
)


class ScaffoldRegistryError(ValueError):
    """A blueprint registration conflicts with an already-registered blueprint."""


BlueprintRegistry = dict[tuple[str, int], ScaffoldBlueprint]

BLUEPRINT_REGISTRY: BlueprintRegistry = {}


def register_blueprint(
    blueprint: ScaffoldBlueprint, *, registry: BlueprintRegistry | None = None
) -> None:
    target = BLUEPRINT_REGISTRY if registry is None else registry
    key = (blueprint.blueprint_id, blueprint.version)
    if key in target:
        raise ScaffoldRegistryError(
            f"blueprint {blueprint.blueprint_id!r} version {blueprint.version} "
            "is already registered."
        )
    target[key] = blueprint


def select_blueprint(
    detected: ScaffoldDetectionResult,
    category: str,
    requested_version: int | None = None,
    *,
    registry: BlueprintRegistry | None = None,
) -> ScaffoldBlueprint | None:
    source = BLUEPRINT_REGISTRY if registry is None else registry
    candidates = [blueprint for blueprint in source.values() if blueprint.category == category]
    if requested_version is not None:
        candidates = [
            blueprint for blueprint in candidates if blueprint.version == requested_version
        ]
        return max(candidates, key=lambda blueprint: blueprint.version, default=None)

    framework_specific = [
        blueprint
        for blueprint in candidates
        if blueprint.framework is not None and blueprint.framework in detected.frameworks
    ]
    if framework_specific:
        return max(framework_specific, key=lambda blueprint: blueprint.version)

    framework_agnostic = [blueprint for blueprint in candidates if blueprint.framework is None]
    if framework_agnostic:
        return max(framework_agnostic, key=lambda blueprint: blueprint.version)

    return None


def _identifier_pattern() -> str:
    return r"^[a-z][a-z0-9_]*$"


def _pascal_identifier_pattern() -> str:
    return r"^[A-Z][A-Za-z0-9]*$"


def _default_blueprints() -> tuple[ScaffoldBlueprint, ...]:
    return (
        ScaffoldBlueprint(
            blueprint_id="fastapi_feature_module",
            version=1,
            category="fastapi_feature_module",
            framework="fastapi",
            required_inputs=(
                RequiredInputSpec(
                    name="feature_name",
                    description="Lowercase snake_case feature name, e.g. 'billing'.",
                    validation_pattern=_identifier_pattern(),
                ),
            ),
            generated_files=(
                GeneratedFileSpec(
                    relative_path_template="backend/app/${feature_name}/__init__.py",
                    content_template_ref="fastapi_feature_module/__init__.py.tmpl",
                ),
                GeneratedFileSpec(
                    relative_path_template="backend/app/${feature_name}/router.py",
                    content_template_ref="fastapi_feature_module/router.py.tmpl",
                ),
                GeneratedFileSpec(
                    relative_path_template="backend/app/${feature_name}/service.py",
                    content_template_ref="fastapi_feature_module/service.py.tmpl",
                ),
            ),
            validation_commands=("python -m compileall backend/app/${feature_name}",),
        ),
        ScaffoldBlueprint(
            blueprint_id="react_ts_feature_component",
            version=1,
            category="react_ts_feature_component",
            framework="react",
            required_inputs=(
                RequiredInputSpec(
                    name="component_name",
                    description="PascalCase React component name, e.g. 'BillingSummary'.",
                    validation_pattern=_pascal_identifier_pattern(),
                ),
            ),
            generated_files=(
                GeneratedFileSpec(
                    relative_path_template=(
                        "frontend/src/components/${component_name}/${component_name}.tsx"
                    ),
                    content_template_ref="react_ts_feature_component/Component.tsx.tmpl",
                ),
                GeneratedFileSpec(
                    relative_path_template="frontend/src/components/${component_name}/index.ts",
                    content_template_ref="react_ts_feature_component/index.ts.tmpl",
                ),
            ),
        ),
        ScaffoldBlueprint(
            blueprint_id="python_package",
            version=1,
            category="python_package",
            framework=None,
            required_inputs=(
                RequiredInputSpec(
                    name="package_name",
                    description="Lowercase snake_case package name.",
                    validation_pattern=_identifier_pattern(),
                ),
            ),
            generated_files=(
                GeneratedFileSpec(
                    relative_path_template="${package_name}/__init__.py",
                    content_template_ref="python_package/__init__.py.tmpl",
                ),
                GeneratedFileSpec(
                    relative_path_template="${package_name}/module.py",
                    content_template_ref="python_package/module.py.tmpl",
                ),
            ),
        ),
        ScaffoldBlueprint(
            blueprint_id="pytest_test_module",
            version=1,
            category="pytest_test_module",
            framework=None,
            required_inputs=(
                RequiredInputSpec(
                    name="module_under_test",
                    description="Lowercase snake_case name of the module under test.",
                    validation_pattern=_identifier_pattern(),
                ),
            ),
            generated_files=(
                GeneratedFileSpec(
                    relative_path_template="tests/test_${module_under_test}.py",
                    content_template_ref="pytest_test_module/test_module.py.tmpl",
                ),
            ),
        ),
        ScaffoldBlueprint(
            blueprint_id="route_service_repository_module",
            version=1,
            category="route_service_repository_module",
            framework=None,
            required_inputs=(
                RequiredInputSpec(
                    name="resource_name",
                    description="Lowercase snake_case resource name, e.g. 'invoice'.",
                    validation_pattern=_identifier_pattern(),
                ),
            ),
            generated_files=(
                GeneratedFileSpec(
                    relative_path_template="backend/app/${resource_name}/routes.py",
                    content_template_ref="route_service_repository_module/routes.py.tmpl",
                ),
                GeneratedFileSpec(
                    relative_path_template="backend/app/${resource_name}/service.py",
                    content_template_ref="route_service_repository_module/service.py.tmpl",
                ),
                GeneratedFileSpec(
                    relative_path_template="backend/app/${resource_name}/repository.py",
                    content_template_ref="route_service_repository_module/repository.py.tmpl",
                ),
            ),
        ),
    )


def register_default_blueprints(*, registry: BlueprintRegistry | None = None) -> None:
    target = BLUEPRINT_REGISTRY if registry is None else registry
    for blueprint in _default_blueprints():
        key = (blueprint.blueprint_id, blueprint.version)
        if key in target:
            continue
        target[key] = blueprint


register_default_blueprints()


__all__ = [
    "BlueprintRegistry",
    "BLUEPRINT_REGISTRY",
    "ScaffoldRegistryError",
    "register_blueprint",
    "register_default_blueprints",
    "select_blueprint",
]
