from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from backend.app.project_validation.contracts import (
    AcceptanceEvaluation,
    AcceptanceEvidence,
    AcceptanceResult,
    EvaluationMethod,
    stable_hash,
)


def classify_evaluation_method(criterion: dict[str, Any]) -> EvaluationMethod:
    review_mode = str(criterion.get("review_mode") or criterion.get("verification_mode") or "").lower()
    expected = str(criterion.get("expected_evidence_type") or "").lower()
    text = str(criterion.get("statement") or criterion.get("requirement") or "").lower()
    if "human" in review_mode or "manual" in review_mode:
        return EvaluationMethod.HUMAN_FUNCTIONAL_REVIEW
    if "visual" in review_mode or any(word in text for word in ("responsive", "appearance", "visual")):
        return EvaluationMethod.VISUAL_REVIEW
    if "command" in review_mode or any(word in expected for word in ("test", "build", "lint", "type")):
        return EvaluationMethod.APPROVED_COMMAND
    if any(word in text for word in ("exists", "file", "artifact", "page", "report", "chart")):
        return EvaluationMethod.ARTIFACT_METADATA
    if any(word in text for word in ("code", "route", "import", "configuration")):
        return EvaluationMethod.STATIC_ANALYSIS
    return EvaluationMethod.DETERMINISTIC


def evaluate_acceptance_criteria(
    criteria: list[dict[str, Any]],
    evidence_by_criterion: dict[str, list[dict[str, Any]]],
) -> list[AcceptanceEvaluation]:
    evaluations: list[AcceptanceEvaluation] = []
    now = datetime.now(timezone.utc)
    for criterion in criteria:
        criterion_id = str(criterion.get("criterion_id") or criterion.get("id") or "").strip()
        text = str(criterion.get("statement") or criterion.get("requirement") or "").strip()
        if not criterion_id or not text:
            raise ValueError("Every acceptance criterion must have an ID and text.")
        method = classify_evaluation_method(criterion)
        required = bool(criterion.get("required", True))
        raw_evidence = evidence_by_criterion.get(criterion_id, [])
        evidence: list[AcceptanceEvidence] = []
        explicit_results: list[str] = []
        for index, item in enumerate(raw_evidence[:100]):
            result = str(item.get("result") or item.get("status") or "").lower()
            if result:
                explicit_results.append(result)
            summary = str(item.get("summary") or item.get("message") or "Evidence recorded.")[:2000]
            source = str(item.get("source_reference") or item.get("source") or f"evidence-{index + 1}")[:500]
            evidence.append(AcceptanceEvidence(
                evidence_id=str(item.get("evidence_id") or f"evidence-{uuid4().hex}"),
                evidence_type=str(item.get("evidence_type") or "validation_record")[:120],
                summary=summary, source_reference=source,
                content_hash=item.get("content_hash") or stable_hash({"summary": summary, "source": source}),
                deterministic=bool(item.get("deterministic", True)),
                sensitive=bool(item.get("sensitive", False)),
            ))
        human = method in {EvaluationMethod.HUMAN_FUNCTIONAL_REVIEW, EvaluationMethod.VISUAL_REVIEW}
        failure: str | None = None
        remediation: str | None = None
        if any(value in {"failed", "failure", "error", "blocked"} for value in explicit_results):
            result = AcceptanceResult.FAILED
            confidence = 1.0
            failure = next((item.summary for item in evidence if any(word in item.summary.lower() for word in ("fail", "error", "blocked"))), "The recorded check failed.")
            remediation = "Route the failure through the existing diagnosis, repair, or replanning workflow."
        elif human:
            result = AcceptanceResult.REQUIRES_HUMAN_REVIEW
            confidence = 1.0
        elif not evidence:
            result = AcceptanceResult.BLOCKED if required else AcceptanceResult.NOT_EVALUATED
            confidence = 0.0
            failure = "No validation evidence was available for this criterion."
            remediation = "Run or record the required deterministic check before delivery review."
        elif any(value in {"partial", "partially_passed", "warning"} for value in explicit_results):
            result = AcceptanceResult.PARTIALLY_PASSED
            confidence = 0.8
            failure = "The available evidence only partially satisfies the criterion."
            remediation = "Complete the missing check or request explicit human review."
        elif any(value in {"passed", "pass", "success", "satisfied"} for value in explicit_results):
            if not any(item.deterministic for item in evidence):
                result = AcceptanceResult.BLOCKED
                confidence = 0.3
                failure = "Only non-deterministic evidence was supplied."
                remediation = "Add a deterministic check or human review decision."
            else:
                result = AcceptanceResult.PASSED
                confidence = 1.0
        else:
            result = AcceptanceResult.BLOCKED
            confidence = 0.2
            failure = "The evidence did not include an explicit check outcome."
            remediation = "Record a passed, failed, blocked, or partial result from an approved check."
        evaluations.append(AcceptanceEvaluation(
            evaluation_id=f"acceptance-{uuid4().hex}", criterion_id=criterion_id,
            criterion_text=text, method=method, result=result, evidence=evidence,
            confidence=confidence, blocking=required and result in {
                AcceptanceResult.FAILED, AcceptanceResult.BLOCKED, AcceptanceResult.PARTIALLY_PASSED,
            }, human_review_required=human, failure_explanation=failure,
            suggested_remediation=remediation, evaluated_at=now,
        ))
    return evaluations


__all__ = ["classify_evaluation_method", "evaluate_acceptance_criteria"]
