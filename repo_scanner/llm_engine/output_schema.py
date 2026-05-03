# repo_scanner/llm_engine/output_schema.py
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


Severity = Literal["low", "medium", "high"]

ActionType = Literal[
    "inspect_file",
    "inspect_module",
    "refactor",
    "add_tests",
    "improve_docs",
    "optimize",
    "fix_bug",
    "continue_analysis",
]


class RepoRisk(BaseModel):
    risk: str = Field(..., description="The technical risk or concern.")
    severity: Severity = Field(..., description="Risk severity.")
    evidence: str = Field(
        ..., description="Evidence from scanner, graph, or analysis output."
    )

    @field_validator("severity", mode="before")
    @classmethod
    def normalize_severity(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value


class RecommendedAction(BaseModel):
    action_type: ActionType = Field(..., description="Machine-readable action category.")
    action: str = Field(..., description="Human-readable recommended action.")
    priority: int = Field(
        ..., ge=1, le=5, description="1 is highest priority, 5 is lowest."
    )
    target_area: str = Field(
        ..., description="File, folder, module, or subsystem to inspect/change."
    )
    requires_file_edit: bool = Field(
        ..., description="Whether this action requires modifying files."
    )
    rationale: str = Field(..., description="Why this action matters.")

    @field_validator("action_type", mode="before")
    @classmethod
    def normalize_action_type(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower().replace(" ", "_")
        return value


class RepoDecision(BaseModel):
    repo_identity: str = Field(
        ..., description="Short classification of the repository."
    )
    architecture_summary: str = Field(
        ..., description="Short description of major architecture."
    )
    confidence: float = Field(
        ..., ge=0.0, le=1.0, description="Confidence in this analysis."
    )
    risks: list[RepoRisk] = Field(default_factory=list)
    recommended_actions: list[RecommendedAction] = Field(default_factory=list)
    inspect_next: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
