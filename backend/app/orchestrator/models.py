from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


TaskStatus = Literal[
    "running",
    "completed",
    "blocked",
    "failed",
    "needs_approval",
    "max_steps_reached",
]

ActionName = Literal[
    "search_files",
    "read_file",
    "analyze_ast",
    "run_tests",
    "validate_syntax",
    "propose_patch",
    "apply_patch",
    "final_response",
]


class AdvisorOutput(BaseModel):
    name: str
    label: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    data: dict[str, Any] = Field(default_factory=dict)
    reason: str | None = None


class ToolAction(BaseModel):
    action: ActionName
    args: dict[str, Any] = Field(default_factory=dict)
    reason: str


class ToolResult(BaseModel):
    action: ActionName
    allowed: bool
    success: bool
    output: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    policy_reason: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ValidationState(BaseModel):
    syntax: dict[str, Any] | None = None
    tests: dict[str, Any] | None = None
    patch_scope: dict[str, Any] | None = None
    risk: dict[str, Any] | None = None
    confidence: dict[str, Any] | None = None


class TaskState(BaseModel):
    task_id: str = Field(default_factory=lambda: str(uuid4()))
    goal: str
    workspace: str
    project_path: str = "."
    status: TaskStatus = "running"
    step_count: int = 0
    allow_edits: bool = False
    allow_tests: bool = True

    intent: str | None = None
    advisor_outputs: list[AdvisorOutput] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    candidate_files: list[str] = Field(default_factory=list)
    inspected_files: list[str] = Field(default_factory=list)
    tool_history: list[ToolResult] = Field(default_factory=list)

    proposed_patch: dict[str, Any] | None = None
    validation: ValidationState = Field(default_factory=ValidationState)
    final_response: str | None = None
    stop_reason: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def record_advisor(self, output: AdvisorOutput) -> None:
        self.advisor_outputs.append(output)
        if output.name == "intent" and output.label:
            self.intent = output.label
        if output.name == "file_relevance":
            for path in output.data.get("top_files", []):
                if isinstance(path, str) and path not in self.candidate_files:
                    self.candidate_files.append(path)
        if output.name == "risk":
            self.validation.risk = output.model_dump()
        self.updated_at = datetime.now(timezone.utc)

    def record_tool_result(self, result: ToolResult) -> None:
        self.tool_history.append(result)
        if result.success:
            self.evidence.append(
                {
                    "action": result.action,
                    "output": result.output,
                    "created_at": result.created_at.isoformat(),
                }
            )
        self.updated_at = datetime.now(timezone.utc)


class OrchestratorConfig(BaseModel):
    max_steps: int = Field(default=12, ge=1, le=50)
    max_repeated_actions: int = Field(default=2, ge=1, le=10)
    max_file_bytes: int = Field(default=50_000, ge=1_000, le=500_000)
    command_timeout_seconds: int = Field(default=60, ge=1, le=300)
    auto_run_advisors_each_step: bool = True
    proposer: Literal["scripted", "slm"] = "scripted"
    slm_model: str = "qwen2.5-coder:1.5b"
    slm_base_url: str = "http://localhost:11434"
    slm_timeout_seconds: int = Field(default=90, ge=1, le=300)


class OrchestratorResult(BaseModel):
    task_id: str
    status: TaskStatus
    final_response: str | None
    stop_reason: str | None
    trace: dict[str, Any]
