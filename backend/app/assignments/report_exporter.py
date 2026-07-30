from __future__ import annotations

from pathlib import Path

from backend.app.core.path_utils import normalize_path_for_platform
from backend.app.assignments.schemas import (
    AssignmentEvidenceChecklist,
    AssignmentMarkingReadiness,
    AssignmentReportDraft,
    AssignmentReportExportResult,
    AssignmentRunbook,
)


EXPORT_FILES = (
    "report_outline.md",
    "evidence_checklist.md",
    "runbook.md",
    "marking_readiness.md",
    "appendix_code_checklist.md",
)


def export_report_package(
    workspace_root: str | Path,
    *,
    report_draft: AssignmentReportDraft,
    evidence: AssignmentEvidenceChecklist,
    runbook: AssignmentRunbook,
    marking_readiness: list[AssignmentMarkingReadiness],
    report_folder: str = "report_package",
    overwrite: bool = False,
) -> AssignmentReportExportResult:
    root = normalize_path_for_platform(workspace_root).path.expanduser().resolve()
    target_dir = _safe_dir(root, report_folder)
    target_dir.mkdir(parents=True, exist_ok=True)
    contents = {
        "report_outline.md": report_draft.markdown,
        "evidence_checklist.md": _evidence_markdown(evidence),
        "runbook.md": _runbook_markdown(runbook),
        "marking_readiness.md": _marking_markdown(marking_readiness),
        "appendix_code_checklist.md": _appendix_markdown(evidence),
    }
    created: list[str] = []
    skipped: list[str] = []
    refused: list[str] = []
    for filename in EXPORT_FILES:
        try:
            target = _safe_file(target_dir, filename)
        except ValueError:
            refused.append(filename)
            continue
        relative = target.relative_to(root).as_posix()
        if target.exists() and not overwrite:
            skipped.append(relative)
            continue
        target.write_text(contents[filename], encoding="utf-8")
        created.append(relative)
    return AssignmentReportExportResult(
        output_directory=str(target_dir),
        created_files=created,
        skipped_files=skipped,
        refused_files=refused,
        overwrite=overwrite,
        warnings=["Placeholders remain where screenshots, results, or metrics are missing."],
    )


def _safe_dir(root: Path, folder: str) -> Path:
    folder_path = Path(folder)
    if folder_path.is_absolute() or ".." in folder_path.parts:
        raise ValueError("Report folder must stay inside the workspace.")
    target = (root / folder_path).resolve()
    try:
        target.relative_to(root)
    except ValueError as error:
        raise ValueError("Report folder must stay inside the workspace.") from error
    return target


def _safe_file(target_dir: Path, filename: str) -> Path:
    path = Path(filename)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("Report export filename is unsafe.")
    return (target_dir / path).resolve()


def _evidence_markdown(evidence: AssignmentEvidenceChecklist) -> str:
    lines = [f"# {evidence.title}", "", "| Status | Type | Title | Suggested file |", "| --- | --- | --- | --- |"]
    for item in evidence.items:
        marker = "MISSING SCREENSHOT/RESULT" if item.status == "missing" else item.status.upper()
        lines.append(f"| {marker} | {item.evidence_type} | {item.title} | `{item.suggested_filename}` |")
    return "\n".join(lines) + "\n"


def _runbook_markdown(runbook: AssignmentRunbook) -> str:
    lines = [f"# {runbook.title}", ""]
    for step in runbook.steps:
        lines.extend([
            f"## {step.step_id}: {step.title}",
            step.explanation,
            f"Expected result: {step.expected_result}",
        ])
        if step.command_suggestion:
            lines.append(f"Command suggestion: `{step.command_suggestion['command']}`")
        if step.screenshot_to_take:
            lines.append(f"Screenshot to take: {step.screenshot_to_take}")
        lines.append(f"Troubleshooting: {step.troubleshooting_hint}")
        lines.append("")
    return "\n".join(lines)


def _marking_markdown(readiness: list[AssignmentMarkingReadiness]) -> str:
    lines = ["# Marking Readiness", "", "Estimated readiness only; this is not a final grade.", ""]
    for item in readiness:
        lines.append(f"## {item.assignment_name}")
        lines.append(f"Estimated ready marks: {item.estimated_ready_marks} / {item.total_marks_available}")
        if item.missing_critical_items:
            lines.append("Missing critical items:")
            lines.extend(f"- {missing}" for missing in item.missing_critical_items)
        for criterion in item.criterion_results:
            lines.append(f"- {criterion.status}: {criterion.title} ({criterion.marks} marks) - {criterion.next_action}")
        lines.append("")
    return "\n".join(lines)


def _appendix_markdown(evidence: AssignmentEvidenceChecklist) -> str:
    lines = ["# Appendix Code Checklist", ""]
    code_items = [item for item in evidence.items if item.evidence_type == "code_file"]
    if not code_items:
        lines.append("- [ ] Add code files used for the assignment.")
    for item in code_items:
        lines.append(f"- [ ] {item.title}: `{item.suggested_filename}`")
    lines.append("")
    lines.append("Do not claim results here; link only to actual code and verified evidence.")
    return "\n".join(lines) + "\n"
