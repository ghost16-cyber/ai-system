from __future__ import annotations

from backend.app.project_validation.contracts import FailureCategory, ReliabilityFinding


def recommended_route(category: FailureCategory) -> str:
    return {
        FailureCategory.EXECUTION_DEFECT: "stage8_repair",
        FailureCategory.TEST_FAILURE: "stage8_repair",
        FailureCategory.BUILD_FAILURE: "stage8_repair",
        FailureCategory.REGRESSION: "stage8_repair",
        FailureCategory.SECURITY: "stage8_repair",
        FailureCategory.MISSING_DELIVERABLE: "stage9_replan",
        FailureCategory.UNMET_REQUIREMENT: "stage9_replan",
        FailureCategory.AMBIGUOUS_REQUIREMENT: "stage10_scope_change",
        FailureCategory.SCOPE_MISMATCH: "stage10_scope_change",
        FailureCategory.EXTERNAL_DEPENDENCY: "external_verification",
        FailureCategory.HUMAN_REVIEW: "manual_action",
    }[category]


def group_findings(findings: list[ReliabilityFinding]) -> dict[str, list[ReliabilityFinding]]:
    grouped: dict[str, list[ReliabilityFinding]] = {}
    for finding in findings:
        grouped.setdefault(finding.recommended_route, []).append(finding)
    return grouped


__all__ = ["group_findings", "recommended_route"]
