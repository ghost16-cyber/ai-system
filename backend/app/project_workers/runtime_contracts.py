from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.app.project_control.contracts import content_hash


WORKER_EXECUTION_SPEC_VERSION = "astra.project-workers.execution-spec.v2"
WORKER_EXECUTION_SPEC_LEGACY_VERSION = "astra.project-workers.execution-spec.v1"
WORKER_PROCESS_RESULT_VERSION = "astra.project-workers.process-result.v1"
ISOLATION_PROFILE_VERSION = "astra.project-workers.isolation-profile.v1"
ISOLATION_CAPABILITY_VERSION = "astra.project-workers.isolation-capability.v1"
ISOLATION_RESULT_VERSION = "astra.project-workers.isolation-result.v1"

DEFAULT_ISOLATION_PROFILE_ID = "astra-python-node-v1"
UNCONFIGURED_IMAGE_DIGEST = "sha256:" + ("0" * 64)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class WorkerCommandAction(StrEnum):
    PYTEST = "pytest"
    PYTHON_SCRIPT = "python_script"
    NPM_TEST = "npm_test"
    NPM_RUN_LINT = "npm_run_lint"
    NPM_RUN_BUILD = "npm_run_build"
    NPM_RUN_TYPECHECK = "npm_run_typecheck"
    NODE_TEST = "node_test"
    DOCKER_PS = "docker_ps"


class IsolationBackendKind(StrEnum):
    DOCKER = "docker"


class IsolationNetworkMode(StrEnum):
    NONE = "none"


class IsolationFilesystemMode(StrEnum):
    SNAPSHOT = "snapshot"


class IsolationToolchain(StrEnum):
    PYTHON = "python"
    NODE = "node"


class IsolationProfile(StrictModel):
    schema_version: str = ISOLATION_PROFILE_VERSION
    profile_id: str = Field(min_length=1, max_length=160)
    backend: IsolationBackendKind = IsolationBackendKind.DOCKER
    image_reference: str = Field(min_length=1, max_length=500)
    image_digest: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    network_mode: IsolationNetworkMode = IsolationNetworkMode.NONE
    filesystem_mode: IsolationFilesystemMode = IsolationFilesystemMode.SNAPSHOT
    root_filesystem_read_only: Literal[True] = True
    run_as_user: str = Field(default="65532:65532", pattern=r"^[0-9]+:[0-9]+$")
    supported_toolchains: tuple[IsolationToolchain, ...] = (
        IsolationToolchain.PYTHON,
        IsolationToolchain.NODE,
    )
    pids_limit: int = Field(default=256, ge=16, le=4096)
    cpu_limit: float = Field(default=2.0, ge=0.1, le=64.0)


class IsolationCapability(StrictModel):
    schema_version: str = ISOLATION_CAPABILITY_VERSION
    backend: IsolationBackendKind
    profile_id: str
    available: bool
    image_reference: str
    configured_image_digest: str
    observed_image_digest: str | None = None
    supported_toolchains: tuple[IsolationToolchain, ...] = ()
    supported_actions: tuple[WorkerCommandAction, ...] = ()
    failure_code: str | None = None
    detail: str | None = None


class IsolationExecutionResult(StrictModel):
    schema_version: str = ISOLATION_RESULT_VERSION
    backend: IsolationBackendKind
    profile_id: str
    container_identity: str
    image_reference: str
    image_digest: str
    argv_display: tuple[str, ...]
    working_directory: str
    outcome: str
    exit_code: int | None
    stdout: str
    stderr: str
    output_truncated: bool
    timed_out: bool
    cancelled: bool
    infrastructure_failure: bool
    duration_ms: int = Field(ge=0)
    effective_policy: dict[str, Any]


class ExecutionInputArtifact(StrictModel):
    relative_path: str = Field(min_length=1, max_length=1000)
    sha256: str = Field(min_length=64, max_length=64)


class WorkerExecutionSpec(StrictModel):
    schema_version: str = WORKER_EXECUTION_SPEC_VERSION
    action: WorkerCommandAction
    execution_hash: str = Field(min_length=64, max_length=64)
    command_id: str | None = Field(default=None, min_length=1, max_length=160)
    criterion_id: str | None = Field(default=None, min_length=1, max_length=160)
    criterion_hash: str | None = Field(default=None, min_length=64, max_length=64)
    working_directory: str = Field(default=".", min_length=1, max_length=1000)
    target: str | None = Field(default=None, min_length=1, max_length=1000)
    arguments: tuple[str, ...] = Field(default=(), max_length=40)
    input_artifacts: tuple[ExecutionInputArtifact, ...] = Field(default=(), max_length=100)
    expected_exit_codes: tuple[int, ...] = Field(default=(0,), min_length=1, max_length=20)
    isolation_profile_id: str = Field(default=DEFAULT_ISOLATION_PROFILE_ID, min_length=1, max_length=160)
    image_digest: str = Field(default=UNCONFIGURED_IMAGE_DIGEST, pattern=r"^sha256:[a-f0-9]{64}$")
    network_mode: IsolationNetworkMode = IsolationNetworkMode.NONE
    filesystem_mode: IsolationFilesystemMode = IsolationFilesystemMode.SNAPSHOT


class WorkerProcessResult(StrictModel):
    schema_version: str = WORKER_PROCESS_RESULT_VERSION
    worker_request_id: str
    action: WorkerCommandAction
    outcome: str
    exit_code: int | None
    stdout: str
    stderr: str
    output_truncated: bool
    timed_out: bool
    cancelled: bool
    duration_ms: int = Field(ge=0)
    evidence_id: str
    evidence_hash: str = Field(min_length=64, max_length=64)
    result_hash: str = Field(min_length=64, max_length=64)


def build_execution_spec(
    *,
    action: WorkerCommandAction | str,
    command_id: str | None = None,
    criterion_id: str | None = None,
    criterion_hash: str | None = None,
    working_directory: str = ".",
    target: str | None = None,
    arguments: tuple[str, ...] | list[str] = (),
    input_artifacts: tuple[ExecutionInputArtifact, ...] | list[ExecutionInputArtifact] = (),
    expected_exit_codes: tuple[int, ...] | list[int] = (0,),
    isolation_profile_id: str = DEFAULT_ISOLATION_PROFILE_ID,
    image_digest: str = UNCONFIGURED_IMAGE_DIGEST,
) -> WorkerExecutionSpec:
    data: dict[str, Any] = {
        "schema_version": WORKER_EXECUTION_SPEC_VERSION,
        "action": WorkerCommandAction(action),
        "command_id": command_id,
        "criterion_id": criterion_id,
        "criterion_hash": criterion_hash,
        "working_directory": working_directory,
        "target": target,
        "arguments": tuple(arguments),
        "input_artifacts": tuple(input_artifacts),
        "expected_exit_codes": tuple(expected_exit_codes),
        "isolation_profile_id": isolation_profile_id,
        "image_digest": image_digest,
        "network_mode": IsolationNetworkMode.NONE,
        "filesystem_mode": IsolationFilesystemMode.SNAPSHOT,
    }
    provisional = WorkerExecutionSpec(execution_hash="0" * 64, **data)
    digest = calculate_execution_hash(provisional)
    return provisional.model_copy(update={"execution_hash": digest})


def calculate_execution_hash(spec: WorkerExecutionSpec) -> str:
    return content_hash(_execution_hash_payload(spec.model_dump(mode="json")))


def _execution_hash_payload(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key != "execution_hash"}


__all__ = [
    "DEFAULT_ISOLATION_PROFILE_ID",
    "ExecutionInputArtifact",
    "IsolationBackendKind",
    "IsolationCapability",
    "IsolationExecutionResult",
    "IsolationFilesystemMode",
    "IsolationNetworkMode",
    "IsolationProfile",
    "IsolationToolchain",
    "UNCONFIGURED_IMAGE_DIGEST",
    "WorkerCommandAction",
    "WorkerExecutionSpec",
    "WorkerProcessResult",
    "build_execution_spec",
    "calculate_execution_hash",
]
