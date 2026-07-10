from __future__ import annotations

from backend.app.assignments.evidence import build_evidence_checklist
from backend.app.assignments.extractor import extract_assignment_brief
from backend.app.assignments.task_breakdown import generate_task_breakdown


BRIEF = """
Portfolio
Assignment 1: Kafka + Grafana
Task 1 - Kafka Producer [6 marks]
Build a producer that sends events.
Screenshot required: Producer terminal.
Task 2 - Grafana Dashboard [10 marks]
Create a dashboard.
Screenshot required: Grafana dashboard.
"""


def test_task_breakdown_generates_beginner_friendly_ordered_tasks():
    brief = extract_assignment_brief(BRIEF)
    evidence = build_evidence_checklist(brief)
    breakdown = generate_task_breakdown(brief, evidence=evidence)

    assert [item.order for item in breakdown.tasks] == [1, 2]
    assert breakdown.tasks[0].title == "Kafka Producer"
    assert "Start by reading" in breakdown.tasks[0].explanation
    assert breakdown.tasks[0].expected_output
    assert breakdown.tasks[0].related_evidence
    assert breakdown.tasks[1].difficulty == "hard"


def test_task_breakdown_output_is_deterministic():
    brief = extract_assignment_brief(BRIEF)

    first = generate_task_breakdown(brief).model_dump(mode="json")
    second = generate_task_breakdown(brief).model_dump(mode="json")

    assert first == second
