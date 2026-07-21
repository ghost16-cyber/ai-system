from __future__ import annotations

from enum import StrEnum
from pathlib import PurePosixPath
from typing import Any, Literal

from pydantic import Field

from backend.app.project_control.contracts import StrictModel, content_hash


class ToolchainSupport(StrEnum):
    SUPPORTED = "supported"
    BLOCKED = "blocked"
    CLARIFICATION_REQUIRED = "clarification_required"


class ProjectToolchainRequirement(StrictModel):
    ecosystem: Literal["python", "node", "unknown"]
    manifest_path: str | None = None
    runtime: str
    required_commands: tuple[str, ...] = ()
    dependency_files: tuple[str, ...] = ()


class ProjectToolchainProfile(StrictModel):
    schema_version: Literal["astra.project-toolchain.profile.v1"] = "astra.project-toolchain.profile.v1"
    requirements: tuple[ProjectToolchainRequirement, ...]
    profile_hash: str = Field(min_length=64, max_length=64)
    project_rag_enabled: bool = False


class ProjectToolchainPreflight(StrictModel):
    schema_version: Literal["astra.project-toolchain.preflight.v1"] = "astra.project-toolchain.preflight.v1"
    status: ToolchainSupport
    supported_ecosystems: tuple[str, ...]
    missing_runtimes: tuple[str, ...] = ()
    unsupported_dependencies: tuple[str, ...] = ()
    clarification: str | None = None
    installs_performed: Literal[False] = False


def detect_toolchain_requirements(evidence: dict[str, Any]) -> ProjectToolchainProfile:
    paths = sorted({
        str(value).replace("\\", "/")
        for value in (*tuple(evidence.get("manifests") or ()), *tuple(evidence.get("paths") or ()))
        if isinstance(value, str) and value
    })
    requirements: list[ProjectToolchainRequirement] = []
    python_files = tuple(path for path in paths if PurePosixPath(path).name in {"pyproject.toml", "requirements.txt", "pytest.ini", "setup.cfg"})
    node_files = tuple(path for path in paths if PurePosixPath(path).name in {"package.json", "package-lock.json", "npm-shrinkwrap.json", "tsconfig.json"})
    if python_files or any(path.endswith(".py") for path in paths):
        requirements.append(ProjectToolchainRequirement(
            ecosystem="python", manifest_path=python_files[0] if python_files else None,
            runtime="python", required_commands=("python",), dependency_files=python_files,
        ))
    if node_files or any(path.endswith((".js", ".jsx", ".ts", ".tsx")) for path in paths):
        requirements.append(ProjectToolchainRequirement(
            ecosystem="node", manifest_path=node_files[0] if node_files else None,
            runtime="node", required_commands=("node", "npm"), dependency_files=node_files,
        ))
    if not requirements:
        requirements.append(ProjectToolchainRequirement(ecosystem="unknown", runtime="unknown"))
    material = [item.model_dump(mode="json") for item in requirements]
    return ProjectToolchainProfile(requirements=tuple(requirements), profile_hash=content_hash(material))


def check_runtime_compatibility(
    profile: ProjectToolchainProfile,
    *,
    available_runtimes: dict[str, bool],
    supported_dependencies: set[str] | None = None,
    requested_dependencies: tuple[str, ...] = (),
) -> ProjectToolchainPreflight:
    missing = sorted({
        command for requirement in profile.requirements for command in requirement.required_commands
        if available_runtimes.get(command) is not True
    })
    unsupported = sorted(set(requested_dependencies) - set(supported_dependencies or ()))
    unknown = any(item.ecosystem == "unknown" for item in profile.requirements)
    if missing or unsupported:
        return ProjectToolchainPreflight(
            status=ToolchainSupport.BLOCKED,
            supported_ecosystems=tuple(sorted({item.ecosystem for item in profile.requirements if item.ecosystem != "unknown"})),
            missing_runtimes=tuple(missing), unsupported_dependencies=tuple(unsupported),
            clarification="Required runtime support is unavailable; Astra will not install dependencies automatically.",
        )
    if unknown:
        return ProjectToolchainPreflight(
            status=ToolchainSupport.CLARIFICATION_REQUIRED,
            supported_ecosystems=(), clarification="The project toolchain could not be identified as Python or Node.",
        )
    return ProjectToolchainPreflight(
        status=ToolchainSupport.SUPPORTED,
        supported_ecosystems=tuple(sorted({item.ecosystem for item in profile.requirements})),
    )


__all__ = [
    "ProjectToolchainPreflight", "ProjectToolchainProfile", "ProjectToolchainRequirement",
    "ToolchainSupport", "check_runtime_compatibility", "detect_toolchain_requirements",
]
