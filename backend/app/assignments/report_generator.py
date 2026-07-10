from __future__ import annotations

import hashlib

from backend.app.assignments.evidence import build_evidence_checklist
from backend.app.assignments.planner import build_assignment_plan
from backend.app.assignments.schemas import (
    AssignmentBrief,
    AssignmentEvidenceChecklist,
    AssignmentPlan,
    AssignmentReportDraft,
    ReportSectionDraft,
)


NO_RESULTS_WARNING = "This draft uses placeholders only. It does not invent results, screenshots, dataset findings, or metrics."


def generate_report_draft(
    brief: AssignmentBrief,
    *,
    plan: AssignmentPlan | None = None,
    evidence: AssignmentEvidenceChecklist | None = None,
    project_metadata: dict | None = None,
) -> AssignmentReportDraft:
    plan = plan or build_assignment_plan(brief)
    evidence = evidence or build_evidence_checklist(brief)
    metadata = project_metadata or {}
    sections: list[ReportSectionDraft] = [
        _section("cover", "Cover Page", _cover_page(brief, metadata), needs=False),
        _section("dataset", "Dataset Description", _dataset_description(brief), needs=True),
        _section("environment", "Environment Setup", _environment_setup(brief), needs=True),
        _section("implementation", "Implementation Steps", _implementation_steps(plan), needs=True),
        _section("screenshots", "Screenshots and Evidence", _screenshots(evidence), needs=True),
        _section("appendix", "Code Appendix", _code_appendix(evidence), needs=True),
        _section("analysis", "Analysis Questions", _analysis_questions(brief), needs=True),
        _section("conclusion", "Conclusion", _conclusion(), needs=True),
        _section("marking", "Marking Checklist", _marking_checklist(brief, evidence), needs=True),
    ]
    markdown = "\n\n".join(f"## {section.title}\n\n{section.content}" for section in sections)
    return AssignmentReportDraft(
        title=f"{brief.title} report draft",
        sections=sections,
        markdown=f"# {brief.title} Report Draft\n\n{markdown}\n",
        warnings=[NO_RESULTS_WARNING],
    )


def _section(section_id: str, title: str, content: str, *, needs: bool) -> ReportSectionDraft:
    return ReportSectionDraft(
        section_id=section_id,
        title=title,
        content=content.strip(),
        needs_user_evidence=needs,
    )


def _cover_page(brief: AssignmentBrief, metadata: dict) -> str:
    student = metadata.get("student_name", "[your name]")
    module = metadata.get("module", "[module name]")
    return f"Student: {student}\nModule: {module}\nAssignment: {brief.title}\nSubmission date: [date]"


def _dataset_description(brief: AssignmentBrief) -> str:
    requirements = brief.dataset_requirements or ["[describe the dataset source, fields, size, and any cleaning needed]"]
    return "\n".join(f"- {item}" for item in requirements) + "\n- Evidence needed: add real dataset observations before submission."


def _environment_setup(brief: AssignmentBrief) -> str:
    tools = ", ".join(brief.technologies) if brief.technologies else "[list tools used]"
    return f"Tools used: {tools}\n\nDescribe how the local services, Python environment, and dashboard tools were set up. Add versions and any configuration choices."


def _implementation_steps(plan: AssignmentPlan) -> str:
    lines = []
    for item in plan.checklist:
        if item.group in {"Pipeline/code implementation", "Dashboard", "Data preparation"}:
            lines.append(f"- {item.assignment_name}: {item.title} ({item.required_output})")
    return "\n".join(lines) or "- [add implementation steps]"


def _screenshots(evidence: AssignmentEvidenceChecklist) -> str:
    lines = []
    for item in evidence.items:
        if item.evidence_type in {"screenshot", "dashboard", "terminal_output", "validation_query"}:
            marker = "MISSING USER EVIDENCE" if item.status == "missing" else item.status.upper()
            lines.append(f"- [{marker}] {item.title} - suggested file: `{item.suggested_filename}`")
    return "\n".join(lines) or "- [add screenshots and terminal output]"


def _code_appendix(evidence: AssignmentEvidenceChecklist) -> str:
    code_items = [item for item in evidence.items if item.evidence_type == "code_file"]
    return "\n".join(f"- {item.title}: `{item.suggested_filename}`" for item in code_items) or "- [list key source files]"


def _analysis_questions(brief: AssignmentBrief) -> str:
    questions = brief.analysis_questions
    if not questions:
        return "- [answer assignment analysis questions here]"
    return "\n".join(f"- {question.question}\n  - Answer: [write your answer using your actual implementation evidence]" for question in questions)


def _conclusion() -> str:
    return "Summarise what was built, what worked, what evidence supports it, and any limitations. Do not add results until they have been verified."


def _marking_checklist(brief: AssignmentBrief, evidence: AssignmentEvidenceChecklist) -> str:
    missing = [item.title for item in evidence.items if item.required and item.status == "missing"]
    criteria = [criterion.description for section in brief.sections for criterion in section.marking_criteria]
    lines = [f"- Criterion: {criterion}" for criterion in criteria] or ["- [check each marking criterion from the brief]"]
    if missing:
        lines.append("\nMissing evidence before submission:")
        lines.extend(f"- {item}" for item in missing[:12])
    return "\n".join(lines)


def report_hash(report: AssignmentReportDraft) -> str:
    return hashlib.sha1(report.markdown.encode("utf-8")).hexdigest()[:12]
