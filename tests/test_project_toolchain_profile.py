from backend.app.project_analysis.model_synthesis.toolchain import (
    ToolchainSupport, check_runtime_compatibility, detect_toolchain_requirements,
)


def test_python_and_node_toolchains_are_detected_without_running_or_installing() -> None:
    profile = detect_toolchain_requirements({
        "manifests": ["pyproject.toml", "package.json", "tsconfig.json"],
        "paths": ["src/app.py", "web/app.ts"],
    })
    assert {item.ecosystem for item in profile.requirements} == {"python", "node"}
    preflight = check_runtime_compatibility(
        profile, available_runtimes={"python": True, "node": True, "npm": True},
    )
    assert preflight.status == ToolchainSupport.SUPPORTED
    assert preflight.installs_performed is False


def test_missing_runtime_and_unsupported_dependency_are_typed_blocks() -> None:
    profile = detect_toolchain_requirements({"manifests": ["package.json"]})
    result = check_runtime_compatibility(
        profile, available_runtimes={"node": True, "npm": False},
        supported_dependencies={"react"}, requested_dependencies=("react", "native-unknown"),
    )
    assert result.status == ToolchainSupport.BLOCKED
    assert result.missing_runtimes == ("npm",)
    assert result.unsupported_dependencies == ("native-unknown",)
    assert "will not install" in str(result.clarification)


def test_unknown_toolchain_requires_clarification_and_rag_is_off() -> None:
    profile = detect_toolchain_requirements({"paths": ["README.md"]})
    result = check_runtime_compatibility(profile, available_runtimes={})
    assert result.status == ToolchainSupport.CLARIFICATION_REQUIRED
    assert profile.project_rag_enabled is False
