from __future__ import annotations

import hashlib
import re
from collections import defaultdict

from backend.app.assignments.schemas import (
    AssignmentBrief,
    AssignmentEvidenceChecklist,
    AssignmentEvidenceItem,
    AssignmentEvidenceSummary,
    EvidencePriority,
    EvidenceStatus,
)


SCREENSHOT_HINTS = (
    "docker containers running",
    "producer terminal showing records being sent",
    "producer terminal",
    "consumer running with enriched output",
    "consumer terminal",
    "influxdb data explorer",
    "grafana dashboard",
    "pyspark terminal schema and data quality summary",
    "pyspark terminal",
    "aggregation query results",
    "window function outputs",
    "snowflake object browser",
    "snowflake worksheet query results",
    "snowflake web ui",
    "snowflake worksheet",
    "streamlit dashboard with kpis/charts/table",
    "streamlit dashboard after filter",
    "streamlit dashboard",
    "redis cli",
    "redis cli output",
    "streaming query progress logs",
    "watermark/query plan/logs",
    "watermark",
    "query plan",
    "logs",
)
GENERIC_SCREENSHOT_PATTERNS = (
    r"^screenshot\s+required[:\-\s]*$",
    r"^screen\s*shot\s+required[:\-\s]*$",
    r"^\[?\s*insert\s+screenshot\s+here\s*\]?$",
    r"^every\s+screenshot\s+must\s+be\s+clear\b",
    r"^screenshots?\s+must\s+be\s+clear\b",
    r"^clear\s+screenshot\b",
)
GENERIC_REPORT_PATTERNS = (
    r"^submit\s+one\s+report\s+per\s+assignment\b",
    r"^report\s+format\b",
    r"^include\s+your\s+name\b",
    r"^use\s+headings\b",
)


def build_evidence_checklist(brief: AssignmentBrief) -> AssignmentEvidenceChecklist:
    items: list[AssignmentEvidenceItem] = []
    for section in brief.sections:
        assignment = section.title
        generic_report_added = False
        for requirement in section.screenshot_requirements:
            for screenshot_source in _split_screenshot_requirement(requirement.description):
                if _is_generic_screenshot_requirement(screenshot_source):
                    continue
                optional_screenshot = _is_optional_requirement(requirement.description)
                items.append(
                    _item(
                        assignment,
                        task_name=requirement.task_name or "Screenshot evidence",
                        evidence_type=_screenshot_evidence_type(screenshot_source),
                        title=_evidence_title(screenshot_source),
                        description=screenshot_source,
                        required=not optional_screenshot,
                        source_requirement=requirement.description,
                        priority="optional" if optional_screenshot else "blocker",
                        marks=_extract_marks(requirement.description),
                    )
                )
        for task in section.tasks:
            task_has_specific_screenshot = any(requirement.task_name == task.title for requirement in section.screenshot_requirements)
            items.append(
                _item(
                    assignment,
                    task_name=task.title,
                    evidence_type="code_file",
                    title=f"Code evidence for {task.title[:60]}",
                    description=f"Show the file or notebook that implements: {task.description}",
                    required=not task.optional,
                    source_requirement=task.description,
                    priority="blocker" if not task.optional else "optional",
                    marks=task.marks,
                )
            )
            if _needs_terminal_output(task.description) and not task_has_specific_screenshot:
                items.append(
                    _item(
                        assignment,
                        task_name=task.title,
                        evidence_type="terminal_output",
                        title=f"Terminal output for {task.title[:60]}",
                        description=f"Capture successful output for: {task.description}",
                        required=not task.optional,
                        source_requirement=task.description,
                        priority="blocker" if not task.optional else "optional",
                        marks=task.marks,
                    )
                )
        for question in section.analysis_questions:
            items.append(
                _item(
                    assignment,
                    task_name="Analysis question",
                    evidence_type="report_answer",
                    title=f"Report answer: {question.question[:70]}",
                    description=question.question,
                    required=True,
                    source_requirement=question.question,
                    priority="blocker",
                )
            )
        for requirement in section.report_requirements:
            if _is_generic_report_requirement(requirement):
                if generic_report_added:
                    continue
                generic_report_added = True
                requirement = "One concise report section for the assignment with required screenshots and answers."
                priority: EvidencePriority = "normal"
            else:
                priority = "blocker"
            items.append(
                _item(
                    assignment,
                    task_name="Report section",
                    evidence_type="appendix" if "appendix" in requirement.lower() else "report_answer",
                    title=f"Report evidence: {requirement[:70]}",
                    description=requirement,
                    required=priority == "blocker",
                    source_requirement=requirement,
                    priority=priority,
                    marks=_extract_marks(requirement),
                )
            )
        for criterion in section.marking_criteria:
            if _needs_validation_query(criterion.description):
                items.append(
                    _item(
                        assignment,
                        task_name="Validation query",
                        evidence_type="validation_query",
                        title=f"Validation evidence: {criterion.description[:70]}",
                        description=criterion.description,
                        required=True,
                        source_requirement=criterion.description,
                        priority="blocker",
                        marks=criterion.marks,
                    )
                )
        for bonus in section.bonus_requirements:
            items.append(
                _item(
                    assignment,
                    task_name=bonus.title,
                    evidence_type="code_file",
                    title=f"Optional bonus evidence: {bonus.title[:60]}",
                    description=bonus.description,
                    required=False,
                    source_requirement=bonus.description,
                    priority="optional",
                    marks=bonus.marks,
                )
            )
            if _mentions_screenshot(bonus.description) and not _is_generic_screenshot_requirement(bonus.description):
                items.append(
                    _item(
                        assignment,
                        task_name=bonus.title,
                        evidence_type=_screenshot_evidence_type(bonus.description),
                        title=f"Optional screenshot: {_evidence_title(bonus.description)}",
                        description=bonus.description,
                        required=False,
                        source_requirement=bonus.description,
                        priority="optional",
                        marks=bonus.marks,
                    )
                )
    deduped = _dedupe_items(items)
    return AssignmentEvidenceChecklist(
        title=f"{brief.title} evidence checklist",
        items=deduped,
        required_items=[item for item in deduped if item.required],
        optional_items=[item for item in deduped if not item.required or item.priority == "optional"],
        summary=summarize_evidence(deduped),
    )


def summarize_evidence(items: list[AssignmentEvidenceItem]) -> AssignmentEvidenceSummary:
    by_assignment: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    by_type: dict[str, int] = defaultdict(int)
    for item in items:
        by_assignment[item.assignment_name]["total"] += 1
        by_assignment[item.assignment_name][item.status] += 1
        by_type[item.evidence_type] += 1
    return AssignmentEvidenceSummary(
        total_required=sum(1 for item in items if item.required),
        total_optional=sum(1 for item in items if not item.required or item.priority == "optional"),
        missing_count=sum(1 for item in items if item.status == "missing"),
        required_missing_count=sum(1 for item in items if item.required and item.status == "missing"),
        optional_missing_count=sum(1 for item in items if (not item.required or item.priority == "optional") and item.status == "missing"),
        provided_count=sum(1 for item in items if item.status == "provided"),
        verified_count=sum(1 for item in items if item.status == "verified"),
        by_assignment={key: dict(value) for key, value in sorted(by_assignment.items())},
        by_evidence_type=dict(sorted(by_type.items())),
    )


def update_evidence_status(
    checklist: AssignmentEvidenceChecklist,
    evidence_id: str,
    status: EvidenceStatus,
    *,
    notes: str | None = None,
) -> AssignmentEvidenceChecklist:
    if status not in {"missing", "provided", "verified", "rejected"}:
        raise ValueError(f"Invalid evidence status: {status}")
    updated: list[AssignmentEvidenceItem] = []
    found = False
    for item in checklist.items:
        if item.evidence_id == evidence_id:
            found = True
            updated.append(item.model_copy(update={"status": status, "notes": notes if notes is not None else item.notes}))
        else:
            updated.append(item)
    if not found:
        raise ValueError(f"Evidence item not found: {evidence_id}")
    return AssignmentEvidenceChecklist(
        title=checklist.title,
        items=updated,
        required_items=[item for item in updated if item.required],
        optional_items=[item for item in updated if not item.required or item.priority == "optional"],
        summary=summarize_evidence(updated),
    )


def _item(
    assignment: str,
    *,
    task_name: str,
    evidence_type: str,
    title: str,
    description: str,
    required: bool,
    source_requirement: str,
    priority: EvidencePriority | None = None,
    marks: float | None = None,
) -> AssignmentEvidenceItem:
    extracted_marks = marks if marks is not None else _extract_marks(source_requirement)
    cleaned_title = _strip_marks(_normalize_display_title(title))
    evidence_id = _evidence_id(assignment, task_name, cleaned_title)
    return AssignmentEvidenceItem(
        evidence_id=evidence_id,
        assignment_name=assignment,
        task_name=task_name,
        evidence_type=evidence_type,
        title=cleaned_title,
        description=description,
        required=required,
        source_requirement=source_requirement,
        status="missing",
        priority=priority or ("blocker" if required else "optional"),
        marks=extracted_marks,
        rubric_reference=_rubric_reference(extracted_marks),
        suggested_filename=_suggested_filename(assignment, evidence_type, title),
    )


def _screenshot_evidence_type(text: str) -> str:
    lowered = text.lower()
    if "dashboard" in lowered or "grafana" in lowered or "streamlit" in lowered:
        return "dashboard"
    if "terminal" in lowered or "cli" in lowered or "logs" in lowered:
        return "terminal_output"
    if "worksheet" in lowered or "query" in lowered:
        return "validation_query"
    return "screenshot"


def _evidence_title(text: str) -> str:
    cleaned = _clean_requirement_text(text)
    lowered = cleaned.lower()
    for hint in SCREENSHOT_HINTS:
        if hint in lowered:
            return hint.title()
    return f"Screenshot: {cleaned[:70]}"


def _split_screenshot_requirement(text: str) -> list[str]:
    cleaned = _clean_requirement_text(text)
    cleaned = re.sub(r"(?i)^screenshot\s+required\s*[:\-]?\s*", "", cleaned).strip()
    parts = [
        part.strip(" .")
        for part in re.split(r",|\band\b", cleaned)
        if part.strip(" .")
    ]
    return parts or ([cleaned] if cleaned else [])


def _clean_requirement_text(text: str) -> str:
    cleaned = re.sub(r"\[\s*insert\s+screenshot\s+here\s*\]", " ", text, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip(" .:-")


def _is_generic_screenshot_requirement(text: str) -> bool:
    cleaned = _clean_requirement_text(text).lower()
    if not cleaned:
        return True
    return any(re.search(pattern, cleaned) for pattern in GENERIC_SCREENSHOT_PATTERNS)


def _is_generic_report_requirement(text: str) -> bool:
    cleaned = _clean_requirement_text(text).lower()
    return any(re.search(pattern, cleaned) for pattern in GENERIC_REPORT_PATTERNS)


def _mentions_screenshot(text: str) -> bool:
    return "screenshot" in text.lower() or "screen shot" in text.lower()


def _is_optional_requirement(text: str) -> bool:
    lowered = text.lower()
    return "bonus" in lowered or "optional" in lowered


def _needs_terminal_output(text: str) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in ("producer", "consumer", "pyspark", "spark", "stream", "load", "run"))


def _needs_validation_query(text: str) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in ("sql", "query", "worksheet", "snowflake", "validation"))


def _evidence_id(assignment: str, task_name: str, title: str) -> str:
    digest = hashlib.sha1(f"{assignment}|{task_name}|{_normalize_key(title)}".encode("utf-8")).hexdigest()[:10]
    return f"ev-{digest}"


def _suggested_filename(assignment: str, evidence_type: str, title: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", f"{assignment}-{title}".lower()).strip("-")[:80]
    suffix = "md" if evidence_type in {"report_answer", "appendix"} else "png"
    if evidence_type in {"terminal_output", "validation_query"}:
        suffix = "txt"
    if evidence_type == "code_file":
        suffix = "py"
    return f"{base or 'evidence'}.{suffix}"


def _dedupe_items(items: list[AssignmentEvidenceItem]) -> list[AssignmentEvidenceItem]:
    seen: set[str] = set()
    deduped: list[AssignmentEvidenceItem] = []
    for item in items:
        if _is_generic_screenshot_requirement(item.title) and item.evidence_type in {"screenshot", "dashboard", "terminal_output", "validation_query"}:
            continue
        key = "|".join(
            [
                _normalize_key(item.assignment_name),
                _normalize_key(item.task_name),
                _normalize_key(item.title),
            ]
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _normalize_display_title(text: str) -> str:
    cleaned = _clean_requirement_text(text)
    return re.sub(r"\s+", " ", cleaned).strip()


def _extract_marks(text: str) -> float | None:
    match = re.search(r"(?i)(\d+(?:\.\d+)?)\s*(?:marks?|%)", text)
    return float(match.group(1)) if match else None


def _strip_marks(text: str) -> str:
    stripped = re.sub(r"(?i)\s*\[?\s*\d+(?:\.\d+)?\s*(?:marks?|%)\s*\]?", "", text)
    return re.sub(r"\s+", " ", stripped).strip(" .:-")


def _rubric_reference(marks: float | None) -> str | None:
    if marks is None:
        return None
    return f"{marks:g} marks"


def _normalize_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()
