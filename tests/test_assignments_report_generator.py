from __future__ import annotations

from backend.app.assignments.evidence import build_evidence_checklist
from backend.app.assignments.extractor import extract_assignment_brief
from backend.app.assignments.planner import build_assignment_plan
from backend.app.assignments.report_generator import generate_report_draft


REPORT_BRIEF = """
Big Data Practical Assignment Portfolio

Assignment 2: PySpark + Snowflake + Streamlit
Task: Implement PySpark cleaning and load the result into Snowflake. 25 marks
Screenshot required: PySpark terminal, Snowflake worksheet, Streamlit dashboard.
Analysis question: Compare batch processing with streaming.
Dataset requirement: use the supplied transaction CSV.
"""


def test_report_outline_includes_required_tasks():
    brief = extract_assignment_brief(REPORT_BRIEF)
    draft = generate_report_draft(brief, plan=build_assignment_plan(brief))

    assert "PySpark cleaning" in draft.markdown
    assert "Implementation Steps" in draft.markdown


def test_report_includes_analysis_questions():
    draft = generate_report_draft(extract_assignment_brief(REPORT_BRIEF))

    assert "Compare batch processing with streaming" in draft.markdown
    assert "Analysis Questions" in draft.markdown


def test_report_includes_screenshot_placeholders():
    brief = extract_assignment_brief(REPORT_BRIEF)
    evidence = build_evidence_checklist(brief)
    draft = generate_report_draft(brief, evidence=evidence)

    assert "MISSING USER EVIDENCE" in draft.markdown
    assert "Streamlit" in draft.markdown


def test_report_does_not_invent_fake_results():
    draft = generate_report_draft(extract_assignment_brief(REPORT_BRIEF))
    lowered = draft.markdown.lower()

    assert "99%" not in lowered
    assert "achieved" not in lowered
    assert draft.warnings


def test_report_output_is_deterministic():
    brief = extract_assignment_brief(REPORT_BRIEF)

    first = generate_report_draft(brief).model_dump(mode="json")
    second = generate_report_draft(brief).model_dump(mode="json")

    assert first == second
