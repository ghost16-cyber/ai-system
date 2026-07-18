from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from backend.app.project_control.contracts import content_hash


WORKER_EXECUTION_SPEC_VERSION = "astra.project-workers.execution-spec.v1"
WORKER_PROCESS_RESULT_VERSION = "astra.project-workers.process-result.v1"


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
    }
    provisional = WorkerExecutionSpec(execution_hash="0" * 64, **data)
    digest = calculate_execution_hash(provisional)
    return provisional.model_copy(update={"execution_hash": digest})


def calculate_execution_hash(spec: WorkerExecutionSpec) -> str:
    return content_hash(_execution_hash_payload(spec.model_dump(mode="json")))


def _execution_hash_payload(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key != "execution_hash"}


__all__ = [
    "ExecutionInputArtifact",
    "WorkerCommandAction",
    "WorkerExecutionSpec",
    "WorkerProcessResult",
    "build_execution_spec",
    "calculate_execution_hash",
]
