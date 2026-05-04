# repo_scanner/planner/plan_schema.py

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


PlanMode = Literal["proposal_only"]
StepType = Literal[
    "inspect",
    "analyze",
    "propose_tests",
    "propose_refactor",
    "propose_fix",
    "propose_optimization",
    "propose_docs",
    "skip",
]
TargetKind = Literal["file", "directory", "module", "unknown"]


class PlanStep(BaseModel):
    step_type: StepType
    source_action_type: str
    target: str
    target_kind: TargetKind = "unknown"
    description: str
    priority: int = Field(..., ge=1, le=5)
    allowed_to_modify: bool = False
    requires_approval: bool = False
    rationale: str


class ExecutionPlan(BaseModel):
    mode: PlanMode = "proposal_only"
    summary: str
    steps: list[PlanStep] = Field(default_factory=list)
    skipped_actions: list[str] = Field(default_factory=list)
    
