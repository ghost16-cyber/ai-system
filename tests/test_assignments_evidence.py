from __future__ import annotations

from backend.app.assignments.evidence import build_evidence_checklist, update_evidence_status
from backend.app.assignments.extractor import extract_assignment_brief
from backend.app.assignments.final_readiness import build_final_readiness_report


EVIDENCE_BRIEF = """
Big Data Practical Assignment Portfolio

Assignment 1: Kafka + InfluxDB + Grafana
Task: Build a Kafka producer and consumer. 20 marks
SCREENSHOT REQUIRED: Docker containers running, Producer terminal, Consumer terminal, InfluxDB Data Explorer, Grafana dashboard.
Analysis question: Explain why Kafka fits streaming ingestion?
Bonus: Add Grafana alerting.
"""


def test_screenshot_requirements_become_evidence_items():
    checklist = build_evidence_checklist(extract_assignment_brief(EVIDENCE_BRIEF))

    titles = [item.title for item in checklist.items]
    assert any("Docker Containers Running" in title for title in titles)
    assert any(item.evidence_type in {"screenshot", "dashboard", "terminal_output"} for item in checklist.items)


def test_code_and_report_requirements_become_evidence_items():
    checklist = build_evidence_checklist(extract_assignment_brief(EVIDENCE_BRIEF))

    assert any(item.evidence_type == "code_file" for item in checklist.items)
    assert any(item.evidence_type == "report_answer" for item in checklist.items)


def test_evidence_ids_are_deterministic():
    first = build_evidence_checklist(extract_assignment_brief(EVIDENCE_BRIEF))
    second = build_evidence_checklist(extract_assignment_brief(EVIDENCE_BRIEF))

    assert [item.evidence_id for item in first.items] == [item.evidence_id for item in second.items]


def test_optional_bonus_evidence_is_marked_optional():
    checklist = build_evidence_checklist(extract_assignment_brief(EVIDENCE_BRIEF))

    bonus_items = [item for item in checklist.items if "bonus" in item.title.lower() or "alerting" in item.description.lower()]
    assert bonus_items
    assert all(item.required is False for item in bonus_items)


def test_evidence_summary_counts_are_correct():
    checklist = build_evidence_checklist(extract_assignment_brief(EVIDENCE_BRIEF))

    assert checklist.summary.total_required == sum(1 for item in checklist.items if item.required)
    assert checklist.summary.missing_count == len(checklist.items)
    assert checklist.summary.provided_count == 0
    assert checklist.summary.by_assignment
    assert checklist.summary.by_evidence_type


def test_status_updates_work_safely():
    checklist = build_evidence_checklist(extract_assignment_brief(EVIDENCE_BRIEF))
    target = checklist.items[0]

    updated = update_evidence_status(checklist, target.evidence_id, "provided", notes="Captured locally")

    assert updated.items[0].status == "provided"
    assert updated.items[0].notes == "Captured locally"
    assert updated.summary.provided_count == 1
    assert checklist.items[0].status == "missing"


def test_generic_screenshot_required_alone_is_ignored():
    brief = extract_assignment_brief(
        """
        Assignment 1: Kafka
        SCREENSHOT REQUIRED
        Every screenshot must be clear.
        [Insert screenshot here]
        """
    )

    checklist = build_evidence_checklist(brief)

    assert not any(item.title.lower() == "screenshot required" for item in checklist.items)
    assert not any("insert screenshot" in item.title.lower() for item in checklist.items)


def test_specific_screenshot_requirement_is_kept():
    brief = extract_assignment_brief(
        """
        Assignment 1: Kafka
        Screenshot required: Docker containers running.
        """
    )

    checklist = build_evidence_checklist(brief)

    assert any(item.title == "Docker Containers Running" for item in checklist.items)


def test_duplicate_screenshot_requirement_is_deduped():
    brief = extract_assignment_brief(
        """
        Assignment 1: Kafka
        Screenshot required: Docker containers running.
        Screenshot required: Docker containers running!
        """
    )

    checklist = build_evidence_checklist(brief)
    matches = [item for item in checklist.items if item.title == "Docker Containers Running"]

    assert len(matches) == 1


def test_bonus_screenshot_is_optional():
    brief = extract_assignment_brief(
        """
        Assignment 1: Kafka
        Bonus: Add alerting screenshot in Grafana.
        """
    )

    checklist = build_evidence_checklist(brief)
    bonus_items = [item for item in checklist.items if "alerting" in item.description.lower()]

    assert bonus_items
    assert all(item.required is False for item in bonus_items)
    assert all(item.priority == "optional" for item in bonus_items)


def test_final_readiness_limits_displayed_blockers():
    brief = extract_assignment_brief(
        """
        Assignment 1: Kafka
        Screenshot required: Docker containers running, Producer terminal, Consumer terminal, InfluxDB Data Explorer, Grafana dashboard, PySpark terminal schema and data quality summary, aggregation query results, window function outputs, Snowflake object browser, Snowflake worksheet query results, Streamlit dashboard with KPIs/charts/table, Streamlit dashboard after filter, Redis CLI output, streaming query progress logs, watermark/query plan/logs.
        Analysis question: Explain the implementation?
        """
    )

    readiness = build_final_readiness_report(brief, assignment_number=1)

    assert len(readiness.missing_blockers) <= 11
    assert readiness.missing_blockers[-1].endswith("more missing evidence items")


def test_cleaned_evidence_output_is_deterministic():
    brief = extract_assignment_brief(
        """
        Assignment 1: Kafka
        Screenshot required: Docker containers running, Docker containers running.
        Submit one report per assignment.
        """
    )

    first = build_evidence_checklist(brief).model_dump(mode="json")
    second = build_evidence_checklist(brief).model_dump(mode="json")

    assert first == second


def test_evidence_separates_required_optional_and_extracts_marks():
    brief = extract_assignment_brief(
        """
        Assignment 1: Kafka
        Task 1 - Kafka Producer [6 marks]
        Implement a producer.
        Screenshot required: Producer terminal [6 marks].
        Bonus: Add alerting screenshot in Grafana [2 marks].
        """
    )

    checklist = build_evidence_checklist(brief)

    assert checklist.required_items
    assert checklist.optional_items
    assert any(item.marks == 6 for item in checklist.required_items)
    assert any(item.marks == 2 for item in checklist.optional_items)
    assert not any("marks" in item.title.lower() for item in checklist.items)
    assert checklist.summary.total_optional == len(checklist.optional_items)
