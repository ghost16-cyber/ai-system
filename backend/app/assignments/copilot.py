from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from backend.app.assignments.evidence import build_evidence_checklist
from backend.app.assignments.extractor import extract_assignment_brief
from backend.app.assignments.analysis_planner import generate_analysis_plan
from backend.app.assignments.code_blueprints import generate_code_blueprints
from backend.app.assignments.dashboard_spec import generate_dashboard_spec
from backend.app.assignments.final_readiness import build_final_readiness_report
from backend.app.assignments.marking_checker import check_marking_readiness
from backend.app.assignments.parser import parse_assignment_document
from backend.app.assignments.planner import build_assignment_plan
from backend.app.assignments.report_generator import generate_report_draft
from backend.app.assignments.report_skeleton import generate_report_skeleton
from backend.app.assignments.runbook import generate_assignment_runbook
from backend.app.assignments.task_breakdown import generate_task_breakdown
from backend.app.assignments.schemas import (
    AssignmentBrief,
    AssignmentCopilotResult,
    AssignmentSection,
    ParsedAssignmentDocument,
)
from backend.app.assignments.templates import generate_assignment_template_plan
from backend.app.assignments.workspace_builder import plan_assignment_workspace
from backend.app.commands import suggest_command
from backend.app.datasets.schemas import DatasetProfile
from backend.app.workspace import inspect_workspace


def run_assignment_copilot(
    *,
    text: str | None = None,
    path: str | Path | None = None,
    selected_assignment: int | str | None = "all",
    workspace_path: str | Path | None = None,
    dataset_profile: DatasetProfile | None = None,
    project_metadata: dict | None = None,
) -> AssignmentCopilotResult:
    parsed = _parse_input(text=text, path=path)
    brief = extract_assignment_brief(parsed)
    filtered = _filter_brief(brief, selected_assignment)
    plan = build_assignment_plan(filtered)
    evidence = build_evidence_checklist(filtered)
    template_numbers = _selected_numbers(filtered, selected_assignment)
    templates = [generate_assignment_template_plan(number) for number in template_numbers]
    code_blueprints = [
        generate_code_blueprints(number, dataset_profile=dataset_profile)
        for number in template_numbers
    ]
    analysis_plans = [
        generate_analysis_plan(number, dataset_profile=dataset_profile)
        for number in template_numbers
    ]
    dashboard_specs = [
        generate_dashboard_spec(number, dataset_profile=dataset_profile)
        for number in template_numbers
    ]
    build_plans = [
        plan_assignment_workspace(
            filtered,
            assignment_number=number,
            workspace_root=workspace_path or ".",
            dataset_profile=dataset_profile,
        )
        for number in template_numbers
    ]
    runbooks = [
        generate_assignment_runbook(number, workspace_root=workspace_path or ".")
        for number in template_numbers
    ]
    commands = _safe_commands(template_numbers, workspace_path or ".")
    report = generate_report_draft(filtered, plan=plan, evidence=evidence, project_metadata=project_metadata)
    report_skeleton = generate_report_skeleton(filtered, dataset_profile=dataset_profile, evidence=evidence)
    task_breakdown = generate_task_breakdown(filtered, evidence=evidence)
    marking = check_marking_readiness(filtered, evidence)
    inspection = None
    inspection_payload = None
    if workspace_path:
        try:
            inspection = inspect_workspace(workspace_path)
            inspection_payload = inspection.model_dump(mode="json")
        except (FileNotFoundError, ValueError):
            inspection = None
            inspection_payload = None
    first_number = template_numbers[0] if template_numbers else 1
    final_readiness = build_final_readiness_report(
        filtered,
        assignment_number=first_number,
        dataset_profile=dataset_profile,
        workspace_inspection=inspection,
        code_blueprints=code_blueprints[0] if code_blueprints else None,
        evidence=evidence,
        report_draft=report,
        marking_readiness=marking,
        dashboard_spec=dashboard_specs[0] if dashboard_specs else None,
        runbook=runbooks[0] if runbooks else None,
    )
    return AssignmentCopilotResult(
        parsed_document_summary={
            "document_id": parsed.document_id,
            "title": parsed.title,
            "source_path": parsed.source_path,
            "section_count": len(filtered.sections),
            "warnings": parsed.warnings,
        },
        extracted_assignment_sections=filtered.sections,
        action_plan=plan,
        recommended_starter_files=templates,
        evidence_checklist=evidence,
        safe_next_commands=[command.model_dump(mode="json") for command in commands],
        report_draft=report,
        report_skeleton=report_skeleton,
        task_breakdown=task_breakdown,
        marking_readiness=marking,
        next_recommended_step=_next_step(evidence, templates),
        workspace_inspection=inspection_payload,
        dataset_profile=dataset_profile.model_dump(mode="json") if dataset_profile else None,
        workspace_build_plans=[plan.model_dump(mode="json") for plan in build_plans],
        runbooks=[runbook.model_dump(mode="json") for runbook in runbooks],
        code_blueprints=[blueprint.model_dump(mode="json") for blueprint in code_blueprints],
        analysis_plans=[plan.model_dump(mode="json") for plan in analysis_plans],
        dashboard_specs=[spec.model_dump(mode="json") for spec in dashboard_specs],
        final_readiness=final_readiness.model_dump(mode="json"),
        tools_executed=False,
        files_written=False,
        training_performed=False,
    )


def _parse_input(*, text: str | None, path: str | Path | None) -> ParsedAssignmentDocument:
    if path:
        return parse_assignment_document(path)
    if text and text.strip():
        title = next((line.strip().strip("#").strip() for line in text.splitlines() if line.strip()), "Assignment brief")
        return ParsedAssignmentDocument(
            document_id="assignment-inline",
            title=title,
            source_path="<inline>",
            extracted_text=text,
            created_at=datetime.now(UTC),
            warnings=[],
        )
    raise ValueError("Either assignment document text or path is required.")


def _filter_brief(brief: AssignmentBrief, selected_assignment: int | str | None) -> AssignmentBrief:
    if selected_assignment in (None, "", "all"):
        return brief
    number = int(selected_assignment)
    matching = [
        section
        for index, section in enumerate(brief.sections, start=1)
        if index == number or f"assignment {number}" in section.title.lower()
    ]
    return AssignmentBrief(
        title=f"{brief.title} - Assignment {number}",
        technologies=_section_technologies(matching),
        sections=matching,
        screenshot_requirements=[item for section in matching for item in section.screenshot_requirements],
        marking_criteria=[item for section in matching for item in section.marking_criteria],
        analysis_questions=[item for section in matching for item in section.analysis_questions],
        bonus_requirements=[item for section in matching for item in section.bonus_requirements],
        dataset_requirements=_unique(item for section in matching for item in section.dataset_requirements),
        report_requirements=_unique(item for section in matching for item in section.report_requirements),
    )


def _selected_numbers(brief: AssignmentBrief, selected_assignment: int | str | None) -> list[int]:
    if selected_assignment not in (None, "", "all"):
        return [int(selected_assignment)]
    numbers: list[int] = []
    for index, section in enumerate(brief.sections, start=1):
        lowered = section.title.lower()
        if "assignment 1" in lowered:
            numbers.append(1)
        elif "assignment 2" in lowered:
            numbers.append(2)
        elif "assignment 3" in lowered:
            numbers.append(3)
        elif index in {1, 2, 3}:
            numbers.append(index)
    return sorted(set(number for number in numbers if number in {1, 2, 3}))


def _safe_commands(numbers: list[int], workspace_path: str | Path) -> list:
    root = Path(workspace_path)
    commands = [suggest_command("pytest", root), suggest_command("docker_ps", root)]
    if any(number in {1, 3} for number in numbers):
        commands.append(suggest_command("docker_compose_up", root))
    if any(number in {2, 3} for number in numbers):
        commands.append(suggest_command("streamlit", root, target="dashboard/app.py"))
    return commands


def _next_step(evidence, templates) -> str:
    if evidence.summary.missing_count:
        return "Start by creating the starter files, then collect the first missing evidence item from the checklist."
    if templates:
        return "Review the starter file plan and run the suggested commands manually when ready."
    return "Review the action plan and fill in report placeholders with verified evidence."


def _section_technologies(sections: list[AssignmentSection]) -> list[str]:
    return _unique(tech for section in sections for tech in section.technologies)


def _unique(values) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = str(value).strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            result.append(cleaned)
    return result
