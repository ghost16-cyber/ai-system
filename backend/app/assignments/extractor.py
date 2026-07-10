from __future__ import annotations

import re
from collections import OrderedDict

from backend.app.assignments.schemas import (
    AnalysisQuestion,
    AssignmentBrief,
    AssignmentSection,
    AssignmentTask,
    MarkingCriterion,
    ParsedAssignmentDocument,
    ScreenshotRequirement,
)


TECHNOLOGIES = (
    "Apache Kafka",
    "Kafka",
    "InfluxDB",
    "Grafana",
    "Apache Spark",
    "PySpark Structured Streaming",
    "PySpark",
    "Snowflake",
    "Streamlit",
    "Shiny",
    "Redis",
    "FastAPI",
    "React",
    "Vite",
    "SQLite",
    "pytest",
    "Ollama",
    "Qwen",
)


def extract_assignment_brief(document: ParsedAssignmentDocument | str) -> AssignmentBrief:
    if isinstance(document, ParsedAssignmentDocument):
        title = document.title
        text = document.extracted_text
    else:
        text = str(document)
        title = _first_nonempty_line(text) or "Assignment brief"

    global_instructions = _global_instructions_from_text(text)
    global_report_guidance = [line for line in global_instructions if _is_report_guidance(line)]
    sections = _sections_from_text(text)
    all_technologies = _unique(
        tech
        for section in sections
        for tech in section.technologies
    )
    return AssignmentBrief(
        title=title,
        technologies=all_technologies,
        sections=sections,
        screenshot_requirements=[item for section in sections for item in section.screenshot_requirements],
        marking_criteria=[item for section in sections for item in section.marking_criteria],
        analysis_questions=[item for section in sections for item in section.analysis_questions],
        bonus_requirements=[item for section in sections for item in section.bonus_requirements],
        dataset_requirements=_unique(item for section in sections for item in section.dataset_requirements),
        report_requirements=_unique(item for section in sections for item in section.report_requirements),
        global_instructions=_unique(
            [
                *global_instructions,
                *(item for section in sections for item in section.global_instructions),
            ]
        ),
        report_guidance=_unique([*global_report_guidance, *(item for section in sections for item in section.report_guidance)]),
    )


def _sections_from_text(text: str) -> list[AssignmentSection]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    starts: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        if re.match(r"(?i)^assignment\s+\d+\s*[:\-]", line):
            starts.append((index, line))
    if not starts:
        starts = [(0, _first_nonempty_line(text) or "Assignment")]

    sections: list[AssignmentSection] = []
    for section_index, (start, heading) in enumerate(starts, start=1):
        end = starts[section_index][0] if section_index < len(starts) else len(lines)
        section_lines = lines[start:end]
        sections.append(_section_from_lines(section_index, heading, section_lines))
    return sections


def _global_instructions_from_text(text: str) -> list[str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    starts = [index for index, line in enumerate(lines) if re.match(r"(?i)^assignment\s+\d+\s*[:\-]", line)]
    if not starts:
        return []
    return _unique(line for line in lines[: starts[0]] if _is_generic_instruction(line) or _is_dataset_guidance(line))


def _section_from_lines(index: int, heading: str, lines: list[str]) -> AssignmentSection:
    title = heading.strip("# ")
    assignment_name = title
    technologies = _extract_technologies(" ".join(lines))
    tasks: list[AssignmentTask] = []
    screenshots: list[ScreenshotRequirement] = []
    marking: list[MarkingCriterion] = []
    questions: list[AnalysisQuestion] = []
    bonus: list[AssignmentTask] = []
    datasets: list[str] = []
    reports: list[str] = []
    global_instructions: list[str] = []
    report_guidance: list[str] = []
    current_task: AssignmentTask | None = None
    reading_marking = False
    reading_analysis = False

    for line_index, line in enumerate(lines, start=1):
        if line_index == 1 and _normalize_line(line) == _normalize_line(heading):
            continue
        lowered = line.lower()
        marks = _extract_marks(line)

        if _is_section_mode_heading(line, "marking"):
            reading_marking = True
            reading_analysis = False
            continue
        if _is_section_mode_heading(line, "analysis"):
            reading_analysis = True
            reading_marking = False
            continue
        if _is_section_mode_heading(line, "report"):
            report_guidance.append(line)
            reading_analysis = False
            reading_marking = False
            continue

        if _is_generic_instruction(line):
            if _is_report_guidance(line):
                report_guidance.append(line)
            else:
                global_instructions.append(line)
            continue

        if _is_dataset_guidance(line):
            if _is_specific_dataset_requirement(line):
                datasets.append(line)
            else:
                global_instructions.append(line)
            continue

        task_title = _extract_task_title(line)
        if task_title is not None:
            task = AssignmentTask(
                task_id=f"a{index}-task-{len(tasks) + 1}",
                title=task_title,
                description=line,
                technologies=_extract_technologies(line) or technologies,
                marks=marks,
                required_output=_required_output(line),
                optional=False,
            )
            tasks.append(task)
            current_task = task
            reading_marking = False
            reading_analysis = False
            if marks is not None:
                marking.append(
                    MarkingCriterion(
                        criterion_id=f"a{index}-mark-{len(marking) + 1}",
                        description=line,
                        marks=marks,
                        assignment_name=assignment_name,
                    )
                )
            continue

        if reading_marking and (marks is not None or _looks_like_marking_criterion(line)):
            marking.append(
                MarkingCriterion(
                    criterion_id=f"a{index}-mark-{len(marking) + 1}",
                    description=line,
                    marks=marks,
                    assignment_name=assignment_name,
                )
            )
            continue

        if reading_analysis or _looks_like_analysis_question(line):
            questions.append(
                AnalysisQuestion(
                    question_id=f"a{index}-question-{len(questions) + 1}",
                    question=line,
                    assignment_name=assignment_name,
                )
            )
            continue

        if _looks_like_bonus_item(line):
            bonus.append(
                AssignmentTask(
                    task_id=f"a{index}-bonus-{len(bonus) + 1}",
                    title=_short_title(line),
                    description=line,
                    technologies=_extract_technologies(line) or technologies,
                    marks=marks,
                    required_output=_required_output(line),
                    optional=True,
                )
            )
            continue

        if _mentions_screenshot(line):
            if _is_generic_screenshot_instruction(line):
                global_instructions.append(line)
                continue
            screenshots.append(
                ScreenshotRequirement(
                    requirement_id=f"a{index}-screenshot-{len(screenshots) + 1}",
                    description=line,
                    assignment_name=assignment_name,
                    task_name=current_task.title if current_task is not None else None,
                )
            )
            continue

        if _is_report_requirement(line):
            reports.append(line)
            continue

        if "mark" in lowered or marks is not None:
            marking.append(
                MarkingCriterion(
                    criterion_id=f"a{index}-mark-{len(marking) + 1}",
                    description=line,
                    marks=marks,
                    assignment_name=assignment_name,
                )
            )

    if not tasks:
        tasks.append(
            AssignmentTask(
                task_id=f"a{index}-task-1",
                title=title,
                description="Review assignment requirements and identify deliverables.",
                technologies=technologies,
                required_output="Requirement checklist",
            )
        )
    return AssignmentSection(
        section_id=f"assignment-{index}",
        title=title,
        technologies=technologies,
        tasks=tasks,
        screenshot_requirements=screenshots,
        marking_criteria=marking,
        analysis_questions=questions,
        bonus_requirements=bonus,
        dataset_requirements=_unique(datasets),
        report_requirements=_unique(reports),
        global_instructions=_unique(global_instructions),
        report_guidance=_unique(report_guidance),
    )


def _extract_technologies(text: str) -> list[str]:
    lowered = text.lower()
    return _unique(tech for tech in TECHNOLOGIES if tech.lower() in lowered)


def _extract_marks(text: str) -> float | None:
    match = re.search(r"(?i)(\d+(?:\.\d+)?)\s*(?:marks?|%)", text)
    return float(match.group(1)) if match else None


def _extract_task_title(line: str) -> str | None:
    cleaned = line.strip()
    match = re.match(r"(?i)^task\s*(?:\d+)?\s*(?:[—–-]|:)\s*(.+)$", cleaned)
    if not match:
        return None
    title = re.sub(r"(?i)\s*\[?\s*\d+(?:\.\d+)?\s*marks?\s*\]?", "", match.group(1)).strip(" .:-")
    return _short_title(title or cleaned)


def _looks_like_analysis_question(line: str) -> bool:
    lowered = line.lower()
    if _is_generic_instruction(line):
        return False
    return (
        "?" in line
        or "analysis question" in lowered
        or lowered.startswith(("analyse", "analyze", "explain", "discuss", "compare", "why "))
    )


def _is_section_mode_heading(line: str, mode: str) -> bool:
    cleaned = line.strip().lower().strip(":")
    if mode == "marking":
        return cleaned in {"marking criteria", "marking scheme", "marks breakdown", "assessment criteria"}
    if mode == "analysis":
        return cleaned in {"analysis", "analysis questions", "questions", "report questions"}
    if mode == "report":
        return cleaned in {"report", "report requirements", "report guidance", "submission requirements"}
    return False


def _is_generic_instruction(line: str) -> bool:
    lowered = _normalize_line(line)
    generic_patterns = (
        r"\beach assignment is completed in groups of\b",
        r"\bgroups? of two\b",
        r"\bregister (your )?dataset\b",
        r"\bdataset selection\b",
        r"\bsuggested dataset sources\b",
        r"\bkeep (the )?report concise\b",
        r"\bsubmit one report per assignment\b",
        r"\bevery screenshot must be clear\b",
        r"\bscreenshots? must be clear\b",
        r"\[?\s*insert screenshot here\s*\]?",
        r"^screenshot required$",
    )
    return any(re.search(pattern, lowered) for pattern in generic_patterns)


def _is_report_guidance(line: str) -> bool:
    lowered = _normalize_line(line)
    return any(term in lowered for term in ("report", "submission", "submit one report", "keep report concise", "portfolio"))


def _is_dataset_guidance(line: str) -> bool:
    lowered = _normalize_line(line)
    return "dataset" in lowered or "data set" in lowered or "csv" in lowered


def _is_specific_dataset_requirement(line: str) -> bool:
    lowered = _normalize_line(line)
    if any(term in lowered for term in ("suggested dataset", "register dataset", "select a dataset", "choose a dataset", "dataset selection")):
        return False
    return any(term in lowered for term in ("provided", "must contain", "csv", "sensor", "transaction", "event", "schema"))


def _mentions_screenshot(line: str) -> bool:
    lowered = line.lower()
    return "screenshot" in lowered or "screen shot" in lowered


def _is_generic_screenshot_instruction(line: str) -> bool:
    lowered = _normalize_line(line)
    return (
        lowered == "screenshot required"
        or "every screenshot must be clear" in lowered
        or "insert screenshot here" in lowered
    )


def _looks_like_marking_criterion(line: str) -> bool:
    lowered = _normalize_line(line)
    return any(term in lowered for term in ("marks", "criterion", "assessed", "graded", "credit", "requires"))


def _looks_like_bonus_item(line: str) -> bool:
    lowered = _normalize_line(line)
    return ("bonus" in lowered or "optional" in lowered) and not _is_generic_instruction(line)


def _is_report_requirement(line: str) -> bool:
    lowered = _normalize_line(line)
    if _is_generic_instruction(line):
        return False
    return any(term in lowered for term in ("report requirement", "include in your report", "explain in the report", "discuss in the report"))


def _required_output(line: str) -> str:
    lowered = line.lower()
    if "dashboard" in lowered:
        return "Dashboard"
    if "screenshot" in lowered:
        return "Screenshot evidence"
    if "report" in lowered or "portfolio" in lowered:
        return "Report section"
    if "dataset" in lowered or "data" in lowered:
        return "Prepared dataset"
    if "stream" in lowered or "pipeline" in lowered:
        return "Working pipeline/code"
    return "Completed task evidence"


def _short_title(line: str) -> str:
    cleaned = re.sub(r"^[-*\d.)\s]+", "", line).strip()
    return cleaned[:90]


def _normalize_line(line: str) -> str:
    return re.sub(r"\s+", " ", line.lower()).strip(" .:-")


def _first_nonempty_line(text: str) -> str | None:
    return next((line.strip().strip("#").strip() for line in text.splitlines() if line.strip()), None)


def _unique(values) -> list[str]:
    ordered = OrderedDict()
    for value in values:
        cleaned = str(value).strip()
        if cleaned:
            ordered.setdefault(cleaned, None)
    return list(ordered)
