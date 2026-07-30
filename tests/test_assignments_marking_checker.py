from __future__ import annotations

from backend.app.assignments.evidence import build_evidence_checklist, update_evidence_status
from backend.app.assignments.extractor import extract_assignment_brief
from backend.app.assignments.marking_checker import check_marking_readiness


MARKING_BRIEF = """
Big Data Practical Assignment Portfolio

Assignment 3: Kafka + PySpark Structured Streaming + Redis + Streamlit
Task: Kafka producer publishes records. 10 marks
Task: Structured Streaming window aggregations with watermarking. 20 marks
Task: Redis output and live Streamlit dashboard. 20 marks
Screenshot required: Kafka logs, Redis CLI, Streamlit dashboard.
Analysis question: Discuss watermarking and late data.
Bonus: Add an extra chart.
"""


def test_marking_criteria_are_detected_from_tasks():
    readiness = check_marking_readiness(extract_assignment_brief(MARKING_BRIEF))

    titles = [item.title for item in readiness[0].criterion_results]
    assert any("Kafka producer" in title for title in titles)
    assert any("watermarking" in title for title in titles)


def test_missing_evidence_lowers_readiness():
    readiness = check_marking_readiness(extract_assignment_brief(MARKING_BRIEF))

    assert readiness[0].estimated_ready_marks == 0
    assert readiness[0].missing_critical_items


def test_verified_evidence_increases_estimated_ready_marks():
    brief = extract_assignment_brief(MARKING_BRIEF)
    evidence = build_evidence_checklist(brief)
    kafka_item = next(item for item in evidence.items if "Kafka" in item.description or "Kafka" in item.title)
    evidence = update_evidence_status(evidence, kafka_item.evidence_id, "verified")

    readiness = check_marking_readiness(brief, evidence)

    assert readiness[0].estimated_ready_marks >= 10


def test_optional_bonus_does_not_block_completion():
    readiness = check_marking_readiness(extract_assignment_brief(MARKING_BRIEF))

    bonus = [item for item in readiness[0].criterion_results if item.status == "optional"]
    assert bonus
    assert all("bonus" not in item.title.lower() or item.title not in readiness[0].missing_critical_items for item in bonus)


def test_report_questions_affect_readiness():
    readiness = check_marking_readiness(extract_assignment_brief(MARKING_BRIEF))

    report_items = [item for item in readiness[0].criterion_results if item.report_required]
    assert report_items
    assert any(item.status == "missing" for item in report_items)


def test_marking_output_is_deterministic():
    brief = extract_assignment_brief(MARKING_BRIEF)

    first = [item.model_dump(mode="json") for item in check_marking_readiness(brief)]
    second = [item.model_dump(mode="json") for item in check_marking_readiness(brief)]

    assert first == second
