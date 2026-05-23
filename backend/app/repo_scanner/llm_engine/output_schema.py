# repo_scanner/llm_engine/output_schema.py
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, PrivateAttr, field_validator, model_validator


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

    @model_validator(mode="after")
    def reject_absence_as_security_evidence(self) -> "RepoRisk":
        risk_text = self.risk.lower()
        evidence_text = self.evidence.lower()
        mentions_security = any(
            term in risk_text or term in evidence_text
            for term in ("security", "vulnerab", "sql injection", "xss")
        )
        absence_markers = (
            "no evidence",
            "no specific evidence",
            "no known",
            "not found",
            "absence of evidence",
        )

        if mentions_security and any(marker in evidence_text for marker in absence_markers):
            raise ValueError(
                "Security risks require positive evidence from context, not absence of evidence."
            )

        return self


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

    # Private attribute – holds the continuous internal score used by the
    # planner for ordering.  It is **not** part of the public schema.
    _score: float = PrivateAttr(default=0.0)

    @field_validator("action_type", mode="before")
    @classmethod
    def normalize_action_type(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower().replace(" ", "_")
        return value

    @model_validator(mode="after")
    def validate_non_editing_actions(self) -> "RecommendedAction":
        if self.action_type in {
            "inspect_file",
            "inspect_module",
            "continue_analysis",
        } and self.requires_file_edit:
            raise ValueError(
                f"{self.action_type} actions must set requires_file_edit to false."
            )
        return self


class RepoDecision(BaseModel):
    repo_identity: str
    architecture_summary: str
    confidence: float = Field(..., ge=0.0, le=1.0)
    risks: list[RepoRisk] = Field(default_factory=list)
    recommended_actions: list[RecommendedAction] = Field(default_factory=list)
    inspect_next: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
