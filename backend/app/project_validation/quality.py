from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from backend.app.project_validation.contracts import (
    AcceptanceEvaluation,
    AcceptanceResult,
    DeliverableManifest,
    QualityAssessment,
    QualityDimension,
    ReadinessDecision,
    RegressionResult,
    stable_hash,
)


def _dimension(name: str, score: float, weight: float, explanation: str, *, evidence_ids: list[str] | None = None, blockers: list[str] | None = None, confidence: float = 1.0) -> QualityDimension:
    return QualityDimension(
        name=name, score=max(0, min(100, score)), weight=weight, confidence=confidence,
        evidence_ids=evidence_ids or [], blocking_findings=blockers or [], explanation=explanation,
    )


def assess_quality(
    *, run_id: str, evaluations: list[AcceptanceEvaluation], manifest: DeliverableManifest,
    regression: RegressionResult, minimum_score: float = 75,
) -> QualityAssessment:
    total = len(evaluations) or 1
    passed = sum(item.result == AcceptanceResult.PASSED for item in evaluations)
    human = sum(item.result == AcceptanceResult.REQUIRES_HUMAN_REVIEW for item in evaluations)
    failed = [item for item in evaluations if item.blocking]
    acceptance_score = 100 * passed / total
    if human:
        acceptance_score += 50 * human / total
    manifest_score = 100 if manifest.complete else max(0, 100 - 25 * len(manifest.missing_deliverable_ids))
    regression_score = 0 if regression.blocking else (75 if regression.unexpected_changes else 100)
    deterministic_evidence = [evidence.evidence_id for item in evaluations for evidence in item.evidence if evidence.deterministic]
    evidence_score = 100 * sum(bool(item.evidence) or item.human_review_required for item in evaluations) / total
    blockers = [f"Acceptance criterion failed: {item.criterion_text}" for item in failed]
    blockers.extend(f"Missing deliverable: {item}" for item in manifest.missing_deliverable_ids)
    if regression.blocking:
        blockers.append(regression.summary)
    dimensions = [
        _dimension("Acceptance coverage", acceptance_score, 0.4, "Measures evidence-backed satisfaction of the approved acceptance criteria.", evidence_ids=deterministic_evidence, blockers=[item for item in blockers if item.startswith("Acceptance")]),
        _dimension("Deliverable completeness", manifest_score, 0.25, "Measures whether the approved deliverables were located and hashed.", blockers=[item for item in blockers if item.startswith("Missing")]),
        _dimension("Regression safety", regression_score, 0.2, "Measures whether validation detected unexpected or destructive changes.", blockers=[regression.summary] if regression.blocking else []),
        _dimension("Evidence quality", evidence_score, 0.15, "Measures whether each criterion has deterministic evidence or an explicit human-review requirement.", evidence_ids=deterministic_evidence, confidence=0.9),
    ]
    weight_total = sum(item.weight for item in dimensions)
    overall = sum(item.score * item.weight for item in dimensions) / weight_total
    uncertainty = min(1.0, (human + sum(not item.evidence and not item.human_review_required for item in evaluations)) / total)
    if blockers:
        decision = ReadinessDecision.REMEDIATION_REQUIRED
    elif overall < minimum_score:
        decision = ReadinessDecision.REMEDIATION_REQUIRED
    else:
        decision = ReadinessDecision.HUMAN_REVIEW_REQUIRED
    payload = {
        "run_id": run_id, "dimensions": [item.model_dump(mode="json") for item in dimensions],
        "overall_score": round(overall, 2), "minimum_score": minimum_score,
        "blocking_findings": blockers, "uncertainty": round(uncertainty, 4),
        "automated_decision": decision.value,
    }
    return QualityAssessment(
        assessment_id=f"quality-{uuid4().hex}", run_id=run_id,
        dimensions=dimensions, overall_score=round(overall, 2), minimum_score=minimum_score,
        blocking_findings=blockers, uncertainty=round(uncertainty, 4),
        automated_decision=decision, assessed_at=datetime.now(timezone.utc),
        assessment_hash=stable_hash(payload),
    )


__all__ = ["assess_quality"]
