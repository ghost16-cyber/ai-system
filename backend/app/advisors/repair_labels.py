from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any


@dataclass(frozen=True)
class RepairAdvisorInput:
    goal: str
    failing_test_file: str | None = None
    failing_test_name: str | None = None
    assertion_summary: str | None = None
    imported_modules: list[str] | None = None
    candidate_files: list[str] | None = None
    inspected_files: list[str] | None = None
    tool_actions: list[str] | None = None


@dataclass(frozen=True)
class RepairAdvisorPrediction:
    bug_type: str
    source_file: str | None
    difficulty: str
    patch_risk: str
    should_use_slm: bool
    confidence: float
    reasons: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RepairAdvisorTrainingExample:
    text: str
    bug_type: str
    source_file: str
    difficulty: str = "easy"
    patch_risk: str = "low"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
