from __future__ import annotations

from backend.app.assignments.evidence import build_evidence_checklist
from backend.app.assignments.schemas import (
    AssignmentBrief,
    AssignmentEvidenceChecklist,
    AssignmentEvidenceItem,
    AssignmentMarkingReadiness,
    MarkingCriterionResult,
)


def check_marking_readiness(
    brief: AssignmentBrief,
    evidence: AssignmentEvidenceChecklist | None = None,
) -> list[AssignmentMarkingReadiness]:
    evidence = evidence or build_evidence_checklist(brief)
    results: list[AssignmentMarkingReadiness] = []
    for section in brief.sections:
        criteria = _criteria_for_section(section)
        evidence_for_section = [item for item in evidence.items if item.assignment_name == section.title]
        criterion_results = [
            _criterion_result(index, criterion, evidence_for_section)
            for index, criterion in enumerate(criteria, start=1)
        ]
        total = sum(item.marks for item in criterion_results)
        ready = sum(item.marks for item in criterion_results if item.status == "ready")
        missing = [item.title for item in criterion_results if item.status == "missing" and item.evidence_required]
        results.append(
            AssignmentMarkingReadiness(
                assignment_name=section.title,
                total_marks_available=total,
                estimated_ready_marks=ready,
                missing_critical_items=missing,
                criterion_results=criterion_results,
            )
        )
    return results


def _criteria_for_section(section) -> list[dict]:
    criteria: list[dict] = []
    for criterion in section.marking_criteria:
        criteria.append(
            {
                "title": _short_title(criterion.description),
                "description": criterion.description,
                "marks": float(criterion.marks or 0),
                "optional": "bonus" in criterion.description.lower() or "optional" in criterion.description.lower(),
            }
        )
    for task in section.tasks:
        criteria.append(
            {
                "title": _short_title(task.description),
                "description": task.description,
                "marks": float(task.marks or 0),
                "optional": task.optional,
            }
        )
    for question in section.analysis_questions:
        criteria.append(
            {
                "title": _short_title(question.question),
                "description": question.question,
                "marks": 0.0,
                "optional": False,
                "report_required": True,
            }
        )
    for bonus in section.bonus_requirements:
        criteria.append(
            {
                "title": _short_title(bonus.description),
                "description": bonus.description,
                "marks": float(bonus.marks or 0),
                "optional": True,
            }
        )
    return _dedupe_criteria(criteria)


def _criterion_result(index: int, criterion: dict, evidence_items: list[AssignmentEvidenceItem]) -> MarkingCriterionResult:
    optional = bool(criterion.get("optional"))
    report_required = bool(criterion.get("report_required")) or _requires_report(criterion["description"])
    matching = _matching_evidence(criterion["description"], evidence_items)
    provided = [item for item in matching if item.status in {"provided", "verified"}]
    verified = [item for item in matching if item.status == "verified"]
    if optional:
        status = "optional"
        reason = "Optional or bonus work; useful but not blocking required completion."
        next_action = "Attempt after required items are ready."
    elif verified:
        status = "ready"
        reason = "At least one matching evidence item is verified."
        next_action = "Keep the evidence linked in the report."
    elif provided:
        status = "partial"
        reason = "Evidence is provided but not verified yet."
        next_action = "Review and mark the evidence verified when it matches the criterion."
    else:
        status = "missing"
        reason = "No matching provided evidence was found."
        next_action = "Add evidence and a short report note for this requirement."
    return MarkingCriterionResult(
        criterion_id=f"criterion-{index}",
        title=criterion["title"],
        marks=float(criterion.get("marks") or 0),
        status=status,
        evidence_required=not optional,
        report_required=report_required,
        reason=reason,
        next_action=next_action,
    )


def _matching_evidence(description: str, evidence_items: list[AssignmentEvidenceItem]) -> list[AssignmentEvidenceItem]:
    keywords = _keywords(description)
    matches = []
    for item in evidence_items:
        haystack = f"{item.title} {item.description} {item.source_requirement}".lower()
        if keywords and any(keyword in haystack for keyword in keywords):
            matches.append(item)
    return matches


def _keywords(text: str) -> list[str]:
    candidates = (
        "kafka",
        "producer",
        "consumer",
        "influxdb",
        "grafana",
        "pyspark",
        "spark",
        "snowflake",
        "streamlit",
        "redis",
        "watermark",
        "window",
        "dashboard",
        "analysis",
        "query",
        "sql",
    )
    lowered = text.lower()
    return [item for item in candidates if item in lowered]


def _requires_report(text: str) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in ("analysis", "explain", "discuss", "compare", "report", "question", "?"))


def _short_title(text: str) -> str:
    cleaned = text.strip()
    return cleaned[:90]


def _dedupe_criteria(criteria: list[dict]) -> list[dict]:
    seen: set[str] = set()
    result: list[dict] = []
    for criterion in criteria:
        key = criterion["description"].lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(criterion)
    return result
