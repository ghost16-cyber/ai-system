from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.assignments.copilot import run_assignment_copilot
from backend.app.main import create_app


COPILOT_BRIEF = """
Big Data Practical Assignment Portfolio

Assignment 1: Kafka + InfluxDB + Grafana
Task: Build a Kafka producer and Grafana dashboard. 20 marks
Screenshot required: Docker containers running and Grafana dashboard.
Analysis question: Explain Kafka ingestion?

Assignment 2: PySpark + Snowflake + Streamlit
Task: Implement PySpark cleaning and Snowflake loading. 25 marks
Screenshot required: Snowflake worksheet and Streamlit dashboard.
Analysis question: Compare batch and streaming.
"""


def test_integrated_copilot_works_on_mini_assignment_document(tmp_path: Path):
    result = run_assignment_copilot(text=COPILOT_BRIEF, workspace_path=tmp_path)

    assert result.parsed_document_summary["section_count"] == 2
    assert result.action_plan.checklist
    assert result.evidence_checklist.items
    assert result.report_draft.sections
    assert result.marking_readiness
    assert result.tools_executed is False


def test_selected_assignment_filtering_works(tmp_path: Path):
    result = run_assignment_copilot(text=COPILOT_BRIEF, selected_assignment=2, workspace_path=tmp_path)

    assert result.parsed_document_summary["section_count"] == 1
    assert all("Assignment 2" in section.title for section in result.extracted_assignment_sections)
    assert [plan.assignment_number for plan in result.recommended_starter_files] == [2]


def test_copilot_does_not_write_files_unless_explicit_writer_is_used(tmp_path: Path):
    before = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))
    result = run_assignment_copilot(text=COPILOT_BRIEF, selected_assignment=1, workspace_path=tmp_path)
    after = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))

    assert before == after
    assert result.files_written is False
    assert all(command["executed"] is False for command in result.safe_next_commands)


def test_copilot_output_is_deterministic(tmp_path: Path):
    first = run_assignment_copilot(text=COPILOT_BRIEF, selected_assignment=1, workspace_path=tmp_path).model_dump(mode="json")
    second = run_assignment_copilot(text=COPILOT_BRIEF, selected_assignment=1, workspace_path=tmp_path).model_dump(mode="json")

    assert first == second


def test_copilot_endpoint_returns_structured_guidance(tmp_path: Path):
    with TestClient(create_app(tmp_path / "app.db", workspace_root=tmp_path)) as client:
        response = client.post(
            "/assignments/copilot/run",
            json={"text": COPILOT_BRIEF, "selected_assignment": "all", "workspace_path": "."},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["action_plan"]["checklist"]
    assert body["evidence_checklist"]["items"]
    assert body["report_draft"]["markdown"]
    assert body["marking_readiness"]
    assert body["tools_executed"] is False
    assert body["files_written"] is False
