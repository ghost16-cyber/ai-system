from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


FAILURE_EVIDENCE_VERSION = "astra.project-failure-evidence.v1"
MAX_STORED_DIAGNOSTIC_CHARS = 30_000
MAX_MODEL_FAILURE_CHARS = 18_000
MAX_STDOUT_CHARS = 10_000
MAX_STDERR_CHARS = 10_000
MAX_DIAGNOSTICS = 80
MAX_FAILING_TESTS = 30
MAX_TRACEBACK_FRAMES = 30
MAX_REFERENCED_FILES = 20
MAX_IDENTICAL_DIAGNOSTICS = 3


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class FailureDiagnostic(StrictModel):
    diagnostic_id: str
    tool: Literal[
        "pytest", "python", "typescript", "eslint", "vite", "json", "yaml",
        "astra_virtual_validation", "generic",
    ]
    reason_code: str = Field(max_length=80)
    severity: Literal["error", "warning", "unknown"] = "error"
    message: str = Field(max_length=1000)
    relative_path: str | None = None
    line: int | None = Field(default=None, ge=1)
    column: int | None = Field(default=None, ge=1)
    test_name: str | None = Field(default=None, max_length=300)
    exception_type: str | None = Field(default=None, max_length=120)
    expected_summary: str | None = Field(default=None, max_length=300)
    actual_summary: str | None = Field(default=None, max_length=300)
    relevant_symbol: str | None = Field(default=None, max_length=200)


class TracebackFrame(StrictModel):
    relative_path: str
    line: int = Field(ge=1)
    function: str | None = Field(default=None, max_length=200)


class ProjectFailureEvidence(StrictModel):
    contract_version: Literal["astra.project-failure-evidence.v1"]
    evidence_id: str
    project_job_id: str
    conversation_id: str
    folder_access_id: str
    parent_patch_id: str
    patch_application_id: str
    command_plan_id: str
    command_execution_id: str
    command_category: str = Field(max_length=80)
    command_identity: str = Field(max_length=200)
    working_directory: str = Field(max_length=300)
    root_fingerprint_before: str
    root_fingerprint_after: str
    project_state_hash: str
    execution_started_at: str | None
    execution_finished_at: str | None
    exit_code: int | None
    timed_out: bool
    output_truncated: bool
    stdout_summary: str = Field(max_length=MAX_STDOUT_CHARS)
    stderr_summary: str = Field(max_length=MAX_STDERR_CHARS)
    diagnostics: list[FailureDiagnostic] = Field(max_length=MAX_DIAGNOSTICS)
    failing_tests: list[str] = Field(max_length=MAX_FAILING_TESTS)
    traceback_frames: list[TracebackFrame] = Field(max_length=MAX_TRACEBACK_FRAMES)
    referenced_files: list[str] = Field(max_length=MAX_REFERENCED_FILES)
    validation_tool: str = Field(max_length=100)
    output_hash: str
    redaction_summary: list[str] = Field(max_length=20)
    uncertainty_codes: list[str] = Field(max_length=20)
    created_at: str
    status: Literal["captured", "stale", "consumed", "invalidated"] = "captured"


__all__ = [
    "FAILURE_EVIDENCE_VERSION", "FailureDiagnostic", "MAX_DIAGNOSTICS",
    "MAX_FAILING_TESTS", "MAX_IDENTICAL_DIAGNOSTICS", "MAX_MODEL_FAILURE_CHARS",
    "MAX_REFERENCED_FILES", "MAX_STDERR_CHARS", "MAX_STDOUT_CHARS",
    "MAX_STORED_DIAGNOSTIC_CHARS", "MAX_TRACEBACK_FRAMES", "ProjectFailureEvidence",
    "StrictModel", "TracebackFrame",
]
