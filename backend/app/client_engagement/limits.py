from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta


@dataclass(frozen=True, slots=True)
class EngagementLimits:
    max_evidence_items: int = 80
    max_evidence_excerpt_chars: int = 1200
    max_requirements: int = 100
    max_deliverables: int = 20
    max_milestones: int = 20
    max_acceptance_criteria_per_deliverable: int = 8
    max_clarification_rounds: int = 3
    max_questions_per_round: int = 3
    max_total_questions: int = 8
    max_assumptions: int = 30
    max_scope_revisions: int = 8
    max_model_retries: int = 2
    max_project_launch_attempts: int = 3
    small_estimate_threshold: int = 3
    medium_estimate_threshold: int = 7
    large_estimate_threshold: int = 12
    staleness_threshold: timedelta = timedelta(hours=24)
    max_history_items: int = 100
    max_public_evidence_items: int = 30
    max_audit_metadata_chars: int = 4_000


STAGE10_LIMITS = EngagementLimits()


__all__ = ["EngagementLimits", "STAGE10_LIMITS"]
