from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from backend.app.hardware_ai_optimizer.schemas import HardwareReport


Availability = Literal["available", "missing", "unknown"]
CapabilityStatus = Literal["good", "limited", "unavailable", "unknown"]
LocalFit = Literal["good", "limited", "poor", "unknown"]
PlanDecision = Literal["allow", "downgrade", "block"]
ExecutionDevice = Literal["cpu", "cuda", "hybrid"]


class ToolStatus(BaseModel):
    name: str
    kind: Literal["command", "python_package"]
    available: Availability
    version: str | None = None
    command: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class Capability(BaseModel):
    name: str
    status: CapabilityStatus
    reason: str
    recommended_tools: list[str] = Field(default_factory=list)
    limits: list[str] = Field(default_factory=list)


class RuntimePolicy(BaseModel):
    low_vram_mode: bool
    prefer_quantized_models: bool
    avoid_large_models: bool
    prefer_rag_over_finetuning: bool
    prefer_cpu_fallback: bool
    cpu_fallback_allowed: bool
    max_recommended_local_model_billion_params: float | None = None
    notes: list[str] = Field(default_factory=list)


class TaskOptimization(BaseModel):
    task_type: str
    local_fit: LocalFit
    suggested_runtime: str
    recommended_tools: list[str] = Field(default_factory=list)
    settings: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)


class PlanValidationResult(BaseModel):
    allowed: bool
    decision: PlanDecision
    reason: str
    requested_plan: dict[str, Any] = Field(default_factory=dict)
    recommended_plan: dict[str, Any] = Field(default_factory=dict)
    blocked_signals: list[str] = Field(default_factory=list)


class ExecutionProfile(BaseModel):
    profile_version: str = "runtime_execution_profile_v1"
    task_type: str
    strategy: str
    runtime: str
    device: ExecutionDevice
    settings: dict[str, Any] = Field(default_factory=dict)
    required_tools: list[str] = Field(default_factory=list)
    optional_tools: list[str] = Field(default_factory=list)
    safeguards: list[str] = Field(default_factory=list)
    source_plan: dict[str, Any] = Field(default_factory=dict)


class RuntimeContext(BaseModel):
    hardware: HardwareReport
    tools: list[ToolStatus]
    capabilities: list[Capability]
    policy: RuntimePolicy
    task_optimization: TaskOptimization
    slm_context: dict[str, Any]
