from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from backend.app.assignments.copilot import run_assignment_copilot
from backend.app.assignments.project_manifest import build_assignment_manifest, write_assignment_manifest


BRIEF = """
Portfolio
Assignment 1: Kafka + InfluxDB + Grafana
Task 1 - Kafka Producer [6 marks]
Implement a producer.
Screenshot required: Producer terminal.
Analysis question: Explain Kafka ingestion?
"""


def _copilot_result(tmp_path: Path) -> dict:
    result = run_assignment_copilot(text=BRIEF, selected_assignment=1, workspace_path=tmp_path)
    return result.model_dump(mode="json")


def test_manifest_generated_from_copilot_output(tmp_path: Path):
    manifest = build_assignment_manifest(
        _copilot_result(tmp_path),
        assignment_number=1,
        dataset_path="data/events.csv",
        document_path="assignment_inputs/brief.docx",
        last_updated=datetime(2026, 7, 9, tzinfo=UTC),
    )

    assert manifest.assignment_number == 1
    assert manifest.dataset_path == "data/events.csv"
    assert "producer.py" in manifest.generated_files


def test_manifest_includes_files_evidence_and_readiness(tmp_path: Path):
    manifest = build_assignment_manifest(_copilot_result(tmp_path), assignment_number=1, last_updated=datetime(2026, 7, 9, tzinfo=UTC))

    assert manifest.generated_files
    assert manifest.evidence_checklist["items"]
    assert manifest.runbook_steps
    assert manifest.readiness_level in {"not_started", "in_progress", "almost_ready", "ready_for_review"}


def test_manifest_write_is_safe_and_skips_existing_by_default(tmp_path: Path):
    manifest = build_assignment_manifest(_copilot_result(tmp_path), assignment_number=1, last_updated=datetime(2026, 7, 9, tzinfo=UTC))
    first = write_assignment_manifest(tmp_path, manifest)
    second = write_assignment_manifest(tmp_path, manifest)

    assert first.written is True
    assert second.skipped is True
    assert (tmp_path / "assignment_manifest.json").exists()


def test_manifest_write_overwrites_when_requested(tmp_path: Path):
    manifest = build_assignment_manifest(_copilot_result(tmp_path), assignment_number=1, last_updated=datetime(2026, 7, 9, tzinfo=UTC))
    write_assignment_manifest(tmp_path, manifest)
    result = write_assignment_manifest(tmp_path, manifest, overwrite=True)

    assert result.written is True
    assert result.overwrite is True


def test_manifest_contains_no_credentials(tmp_path: Path):
    payload = _copilot_result(tmp_path)
    payload["secret_token"] = "real-value"
    manifest = build_assignment_manifest(payload, assignment_number=1, last_updated=datetime(2026, 7, 9, tzinfo=UTC))
    write_assignment_manifest(tmp_path, manifest)

    text = (tmp_path / "assignment_manifest.json").read_text(encoding="utf-8")
    data = json.loads(text)
    assert "real-value" not in text
    assert data["credentials_included"] is False


def test_manifest_structure_is_deterministic(tmp_path: Path):
    payload = _copilot_result(tmp_path)
    timestamp = datetime(2026, 7, 9, tzinfo=UTC)
    first = build_assignment_manifest(payload, assignment_number=1, last_updated=timestamp).model_dump(mode="json")
    second = build_assignment_manifest(payload, assignment_number=1, last_updated=timestamp).model_dump(mode="json")

    assert first == second
