from __future__ import annotations

from collections import OrderedDict

from backend.app.assignments.schemas import (
    AssignmentBrief,
    AssignmentChecklistItem,
    AssignmentPlan,
)


SUMMARY_GROUPS = [
    "Setup",
    "Data preparation",
    "Pipeline/code implementation",
    "Dashboard",
    "Evidence/screenshots",
    "Report writing",
    "Final marking check",
]


def build_assignment_plan(brief: AssignmentBrief) -> AssignmentPlan:
    items: list[AssignmentChecklistItem] = []
    for section in brief.sections:
        technologies = section.technologies or brief.technologies
        tech_area = ", ".join(technologies[:4]) if technologies else "General"
        items.append(
            AssignmentChecklistItem(
                task_id=f"{section.section_id}-setup",
                title=f"Set up tools for {section.title}",
                assignment_name=section.title,
                technology_area=tech_area,
                required_output="Working local environment and project folders",
                evidence_needed=["Environment notes", "Dependency list"],
                group="Setup",
            )
        )
        if section.dataset_requirements:
            items.append(
                AssignmentChecklistItem(
                    task_id=f"{section.section_id}-data",
                    title=f"Prepare datasets for {section.title}",
                    assignment_name=section.title,
                    technology_area=tech_area,
                    required_output="Clean input dataset ready for the pipeline",
                    evidence_needed=section.dataset_requirements,
                    group="Data preparation",
                )
            )
        for task in section.tasks:
            group = _group_for_task(task.required_output or task.description)
            items.append(
                AssignmentChecklistItem(
                    task_id=task.task_id,
                    title=task.title,
                    assignment_name=section.title,
                    technology_area=", ".join((task.technologies or technologies)[:4]) if (task.technologies or technologies) else "General",
                    required_output=task.required_output or "Completed task evidence",
                    evidence_needed=[task.description],
                    screenshot_needed=_section_has_screenshot(section, task.description),
                    report_section_needed=_section_needs_report(section, task.description),
                    optional=task.optional,
                    group=group,
                )
            )
        for screenshot in section.screenshot_requirements:
            items.append(
                AssignmentChecklistItem(
                    task_id=screenshot.requirement_id,
                    title=f"Capture screenshot evidence: {screenshot.description[:70]}",
                    assignment_name=section.title,
                    technology_area=tech_area,
                    required_output="Screenshot evidence",
                    evidence_needed=[screenshot.description],
                    screenshot_needed=True,
                    group="Evidence/screenshots",
                )
            )
        for question in section.analysis_questions:
            items.append(
                AssignmentChecklistItem(
                    task_id=question.question_id,
                    title=f"Answer analysis question: {question.question[:70]}",
                    assignment_name=section.title,
                    technology_area=tech_area,
                    required_output="Report answer",
                    evidence_needed=[question.question],
                    report_section_needed=True,
                    group="Report writing",
                )
            )
        for requirement in section.report_requirements:
            items.append(
                AssignmentChecklistItem(
                    task_id=f"{section.section_id}-report-{len(items) + 1}",
                    title=f"Write report section for {section.title}",
                    assignment_name=section.title,
                    technology_area=tech_area,
                    required_output="Report section",
                    evidence_needed=[requirement],
                    report_section_needed=True,
                    group="Report writing",
                )
            )
        for bonus in section.bonus_requirements:
            items.append(
                AssignmentChecklistItem(
                    task_id=bonus.task_id,
                    title=f"Optional bonus: {bonus.title}",
                    assignment_name=section.title,
                    technology_area=", ".join((bonus.technologies or technologies)[:4]) if (bonus.technologies or technologies) else "General",
                    required_output=bonus.required_output or "Bonus evidence",
                    evidence_needed=[bonus.description],
                    screenshot_needed=_section_has_screenshot(section, bonus.description),
                    report_section_needed=True,
                    optional=True,
                    group="Pipeline/code implementation",
                )
            )
        items.append(
            AssignmentChecklistItem(
                task_id=f"{section.section_id}-marking-check",
                title=f"Check marking criteria for {section.title}",
                assignment_name=section.title,
                technology_area=tech_area,
                required_output="Final marking checklist",
                evidence_needed=[criterion.description for criterion in section.marking_criteria] or ["Review assignment marking scheme"],
                report_section_needed=True,
                group="Final marking check",
            )
        )
    groups = _group_items(items)
    return AssignmentPlan(
        title=brief.title,
        checklist=items,
        groups=groups,
        summary_groups=SUMMARY_GROUPS,
    )


def _group_items(items: list[AssignmentChecklistItem]) -> dict[str, list[AssignmentChecklistItem]]:
    groups: OrderedDict[str, list[AssignmentChecklistItem]] = OrderedDict((group, []) for group in SUMMARY_GROUPS)
    for item in items:
        groups.setdefault(item.group, []).append(item)
    return dict(groups)


def _group_for_task(text: str) -> str:
    lowered = text.lower()
    if "dashboard" in lowered:
        return "Dashboard"
    if "dataset" in lowered or "data" in lowered:
        return "Data preparation"
    if "screenshot" in lowered:
        return "Evidence/screenshots"
    if "report" in lowered:
        return "Report writing"
    return "Pipeline/code implementation"


def _section_has_screenshot(section, text: str) -> bool:
    return bool(section.screenshot_requirements) or "screenshot" in text.lower()


def _section_needs_report(section, text: str) -> bool:
    lowered = text.lower()
    return bool(section.report_requirements or section.analysis_questions) or "report" in lowered
