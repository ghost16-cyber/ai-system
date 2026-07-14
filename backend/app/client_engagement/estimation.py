from __future__ import annotations

import math
from typing import Any, Iterable

from backend.app.client_engagement.contracts import (
    ENGAGEMENT_SCHEMA_VERSION,
    Deliverable,
    EffortEstimate,
    EffortRange,
    EstimateConfidence,
    RelativeSize,
    Risk,
)
from backend.app.client_engagement.limits import EngagementLimits, STAGE10_LIMITS


def estimate_effort(
    *,
    deliverables: Iterable[Deliverable | dict[str, Any]],
    dependencies: Iterable[Any] = (),
    risks: Iterable[Risk | dict[str, Any]] = (),
    repository_file_count: int = 0,
    testing_requirement_count: int = 0,
    assumptions: Iterable[str] = (),
    limits: EngagementLimits = STAGE10_LIMITS,
) -> EffortEstimate:
    items = [item if isinstance(item, Deliverable) else Deliverable.model_validate(item) for item in deliverables]
    risk_values = [item if isinstance(item, Risk) else Risk.model_validate(item) for item in risks]
    dependency_count = len(list(dependencies))
    criteria_count = sum(len(item.acceptance_criteria) for item in items)
    high_risks = sum(1 for item in risk_values if item.impact == "high" or item.likelihood == "high")
    work_units = max(1, len(items))
    work_units += math.ceil(criteria_count / 4)
    work_units += min(3, dependency_count)
    work_units += min(3, high_risks)
    work_units += 2 if repository_file_count > 250 else 1 if repository_file_count > 80 else 0
    work_units += math.ceil(max(0, testing_requirement_count - 2) / 3)
    work_units = min(20, work_units)
    if work_units <= limits.small_estimate_threshold:
        size = RelativeSize.S if work_units > 1 else RelativeSize.XS
    elif work_units <= limits.medium_estimate_threshold:
        size = RelativeSize.M
    elif work_units <= limits.large_estimate_threshold:
        size = RelativeSize.L
    else:
        size = RelativeSize.XL
    assumption_values = list(dict.fromkeys(str(item).strip() for item in assumptions if str(item).strip()))[:20]
    uncertainty: list[str] = []
    if dependency_count: uncertainty.append(f"{dependency_count} client or third-party dependencies require confirmation or access.")
    if high_risks: uncertainty.append(f"{high_risks} high-impact or high-likelihood risks may add rework.")
    if repository_file_count == 0: uncertainty.append("No authorized repository-size evidence is available yet.")
    if assumption_values: uncertainty.append("Documented assumptions may require revision when confirmed.")
    confidence = EstimateConfidence.HIGH
    if uncertainty: confidence = EstimateConfidence.MEDIUM
    if len(uncertainty) >= 3 or len(assumption_values) >= 3: confidence = EstimateConfidence.LOW
    optimistic_min = max(1, math.floor(work_units * 0.7))
    optimistic_max = max(optimistic_min, work_units)
    expected_min = max(1, math.floor(work_units * 0.9))
    expected_max = max(expected_min, math.ceil(work_units * 1.3))
    pessimistic_min = max(expected_max, math.ceil(work_units * 1.25))
    pessimistic_max = max(pessimistic_min, min(30, math.ceil(work_units * 1.9)))
    return EffortEstimate(
        schema_version=ENGAGEMENT_SCHEMA_VERSION, relative_size=size,
        estimated_work_unit_count=work_units,
        optimistic=EffortRange(schema_version=ENGAGEMENT_SCHEMA_VERSION, minimum=optimistic_min, maximum=optimistic_max),
        expected=EffortRange(schema_version=ENGAGEMENT_SCHEMA_VERSION, minimum=expected_min, maximum=expected_max),
        pessimistic=EffortRange(schema_version=ENGAGEMENT_SCHEMA_VERSION, minimum=pessimistic_min, maximum=pessimistic_max),
        confidence=confidence, uncertainty_drivers=uncertainty,
        assumptions=assumption_values, guaranteed=False,
    )


__all__ = ["estimate_effort"]
