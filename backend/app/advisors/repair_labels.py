from __future__ import annotations

from dataclasses import asdict, dataclass
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
class AdvisorFieldPrediction:
    value: str | None
    confidence: float


@dataclass(frozen=True)
class RepairAdvisorPrediction:
    bug_type: AdvisorFieldPrediction
    source_file: AdvisorFieldPrediction
    difficulty: AdvisorFieldPrediction
    patch_risk: AdvisorFieldPrediction
    should_use_slm: bool
    overall_confidence: float
    reasons: list[str]

    @property
    def confidence(self) -> float:
        return self.overall_confidence

    def to_dict(self) -> dict[str, Any]:
        return {
            "bug_type": asdict(self.bug_type),
            "source_file": asdict(self.source_file),
            "difficulty": asdict(self.difficulty),
            "patch_risk": asdict(self.patch_risk),
            "should_use_slm": self.should_use_slm,
            "overall_confidence": self.overall_confidence,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class RepairAdvisorTrainingExample:
    text: str
    bug_type: str
    source_file: str
    difficulty: str = "easy"
    patch_risk: str = "low"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
