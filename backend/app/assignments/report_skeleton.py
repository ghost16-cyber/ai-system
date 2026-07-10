from __future__ import annotations

from backend.app.assignments.evidence import build_evidence_checklist
from backend.app.assignments.schemas import (
    AssignmentBrief,
    AssignmentEvidenceChecklist,
    AssignmentReportDraft,
    ReportSectionDraft,
)
from backend.app.datasets.schemas import DatasetProfile


def generate_report_skeleton(
    brief: AssignmentBrief,
    *,
    dataset_profile: DatasetProfile | None = None,
    evidence: AssignmentEvidenceChecklist | None = None,
) -> AssignmentReportDraft:
    evidence = evidence or build_evidence_checklist(brief)
    sections = [
        _section("introduction", "Introduction", _introduction(brief)),
        _section("dataset", "Dataset Description", _dataset(dataset_profile)),
        _section("methodology", "Methodology", _methodology(brief)),
        _section("results", "Results", _results()),
        _section("evidence", "Screenshots/Evidence", _evidence(evidence)),
        _section("conclusion", "Conclusion", _conclusion()),
        _section("references", "References", _references()),
    ]
    markdown = "\n\n".join(f"## {section.title}\n\n{section.content}" for section in sections)
    return AssignmentReportDraft(
        title=f"{brief.title} report skeleton",
        sections=sections,
        markdown=f"# {brief.title} Report Skeleton\n\n{markdown}\n",
        warnings=["Skeleton only. Fill results and screenshots from verified work; do not invent outputs."],
    )


def _section(section_id: str, title: str, content: str) -> ReportSectionDraft:
    return ReportSectionDraft(section_id=section_id, title=title, content=content.strip(), needs_user_evidence=True)


def _introduction(brief: AssignmentBrief) -> str:
    assignments = ", ".join(section.title for section in brief.sections) or brief.title
    return f"Briefly introduce the practical assignment scope: {assignments}. State what you built and which tools were used."


def _dataset(profile: DatasetProfile | None) -> str:
    if profile is None:
        return "- Dataset path/source: [add approved dataset]\n- Rows/columns: [profile dataset first]\n- Important fields: [timestamp, numeric, and category fields]\n- Cleaning needed: [describe actual cleaning only]"
    return (
        f"- Dataset path/source: `{profile.dataset_path}`\n"
        f"- Estimated rows: {profile.row_count_estimate}\n"
        f"- Columns: {', '.join(profile.columns)}\n"
        f"- Date/time columns: {', '.join(profile.detected_date_columns) or '[choose manually]'}\n"
        f"- Numeric columns: {', '.join(profile.detected_numeric_columns) or '[choose manually]'}\n"
        f"- Categorical columns: {', '.join(profile.detected_categorical_columns) or '[choose manually]'}"
    )


def _methodology(brief: AssignmentBrief) -> str:
    lines = []
    for section in brief.sections:
        lines.append(f"- {section.title}: describe how each task was implemented and why the chosen tools fit.")
        for task in section.tasks:
            lines.append(f"  - {task.title}: [explain implementation steps and configuration choices]")
    return "\n".join(lines) or "- [describe methodology]"


def _results() -> str:
    return "- Add only real outputs after running your implementation.\n- Include tables, dashboard observations, query outputs, or terminal summaries when verified."


def _evidence(evidence: AssignmentEvidenceChecklist) -> str:
    required = "\n".join(f"- Required: {item.title} -> `{item.suggested_filename}`" for item in evidence.required_items)
    optional = "\n".join(f"- Optional: {item.title} -> `{item.suggested_filename}`" for item in evidence.optional_items)
    return "\n".join(part for part in [required, optional] if part) or "- [add required screenshots and evidence]"


def _conclusion() -> str:
    return "Summarise what was completed, what evidence proves it, and any limitations or next improvements."


def _references() -> str:
    return "- Assignment brief\n- Official documentation for tools used\n- Dataset source citation"
