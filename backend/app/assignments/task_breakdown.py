from __future__ import annotations

from backend.app.assignments.evidence import build_evidence_checklist
from backend.app.assignments.schemas import (
    AssignmentBrief,
    AssignmentEvidenceChecklist,
    AssignmentTaskBreakdown,
    AssignmentTaskBreakdownItem,
)


def generate_task_breakdown(
    brief: AssignmentBrief,
    *,
    evidence: AssignmentEvidenceChecklist | None = None,
) -> AssignmentTaskBreakdown:
    evidence = evidence or build_evidence_checklist(brief)
    items: list[AssignmentTaskBreakdownItem] = []
    order = 1
    for section in brief.sections:
        for task in section.tasks:
            related = [
                item.title
                for item in evidence.items
                if item.assignment_name == section.title and (item.task_name == task.title or task.title.lower() in item.description.lower())
            ]
            items.append(
                AssignmentTaskBreakdownItem(
                    order=order,
                    assignment_name=section.title,
                    task_id=task.task_id,
                    title=task.title,
                    explanation=_explanation(task.description),
                    expected_output=task.required_output or "Completed task evidence",
                    related_evidence=related,
                    difficulty=_difficulty(task.description, task.marks),
                    optional=task.optional,
                )
            )
            order += 1
        for question in section.analysis_questions:
            related = [item.title for item in evidence.items if item.source_requirement == question.question]
            items.append(
                AssignmentTaskBreakdownItem(
                    order=order,
                    assignment_name=section.title,
                    task_id=question.question_id,
                    title="Answer analysis question",
                    explanation="Write a short explanation using only evidence from your actual implementation and screenshots.",
                    expected_output="Report answer",
                    related_evidence=related,
                    difficulty="medium",
                )
            )
            order += 1
    return AssignmentTaskBreakdown(
        title=f"{brief.title} beginner task breakdown",
        tasks=items,
        warnings=["Suggested order only. Astra does not run commands or verify results."],
    )


def _explanation(description: str) -> str:
    return (
        "Start by reading the requirement, then build the smallest working version, "
        f"capture the requested evidence, and write what you observed. Requirement: {description}"
    )


def _difficulty(description: str, marks: float | None) -> str:
    lowered = description.lower()
    if marks is not None and marks <= 5:
        return "easy"
    if marks is not None and marks >= 10:
        return "hard"
    if any(term in lowered for term in ("streaming", "watermark", "snowflake", "grafana", "dashboard", "influxdb")):
        return "hard"
    if any(term in lowered for term in ("load", "clean", "profile", "producer")):
        return "medium"
    return "easy"
