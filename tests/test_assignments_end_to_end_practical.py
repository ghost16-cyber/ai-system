from __future__ import annotations

import json
from pathlib import Path

from backend.app.assignments.project_manifest import build_assignment_manifest, write_assignment_manifest
from backend.app.assignments.copilot import run_assignment_copilot
from backend.app.datasets import profile_csv_dataset


SAMPLE_BRIEF = """
Big Data Practical Assignment Portfolio

Assignment 1: Kafka + InfluxDB + Grafana
Task 1 - Kafka Producer [6 marks]
Build a producer that reads the dataset and sends events.
Screenshot required: Producer terminal showing records being sent.
Task 2 - Grafana Dashboard [9 marks]
Build a dashboard over the ingested metrics.
Screenshot required: Grafana dashboard.
Analysis question: Explain why Kafka is suitable for this ingestion workflow?
Bonus: Add an alerting screenshot in Grafana [2 marks].
"""


def test_end_to_end_practical_assignment_flow(tmp_path: Path):
    dataset = tmp_path / "events.csv"
    dataset.write_text(
        "event_time,value,temperature,site\n"
        "2026-01-01,10,21,A\n"
        "2026-01-02,15,22,B\n",
        encoding="utf-8",
    )
    profile = profile_csv_dataset(dataset, row_count_override=35_000)

    result = run_assignment_copilot(
        text=SAMPLE_BRIEF,
        selected_assignment=1,
        workspace_path=tmp_path,
        dataset_profile=profile,
    )
    payload = result.model_dump(mode="json")
    manifest = build_assignment_manifest(
        payload,
        assignment_number=1,
        dataset_path=str(dataset),
        document_path="inline",
    )
    write_result = write_assignment_manifest(tmp_path, manifest)

    assert payload["task_breakdown"]["tasks"]
    assert payload["evidence_checklist"]["required_items"]
    assert payload["evidence_checklist"]["optional_items"]
    assert payload["report_skeleton"]["sections"]
    assert all(command["executed"] is False for command in payload["safe_next_commands"])
    assert write_result.written is True
    manifest_json = json.loads((tmp_path / "assignment_manifest.json").read_text(encoding="utf-8"))
    assert manifest_json["task_breakdown"]["tasks"]
    assert manifest_json["report_skeleton"]["sections"]
    assert manifest_json["tools_executed"] is False
