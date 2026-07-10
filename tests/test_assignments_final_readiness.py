from __future__ import annotations

from pathlib import Path

from backend.app.assignments.code_blueprints import generate_code_blueprints
from backend.app.assignments.evidence import build_evidence_checklist, update_evidence_status
from backend.app.assignments.extractor import extract_assignment_brief
from backend.app.assignments.final_readiness import build_final_readiness_report
from backend.app.datasets import profile_csv_dataset
from backend.app.workspace.schemas import WorkspaceInspection


BRIEF = """
Portfolio
Assignment 1: Kafka + InfluxDB + Grafana
Task: Build Kafka producer. 10 marks
Screenshot required: Producer terminal, Grafana dashboard.
Analysis question: Explain Kafka ingestion?
Bonus: Add alerting.
"""


def _profile(tmp_path: Path):
    path = tmp_path / "events.csv"
    path.write_text("event_time,value,temp,site\n2026-01-01,10,20,A\n", encoding="utf-8")
    return profile_csv_dataset(path, row_count_override=25_000)


def _workspace(paths: list[str], tmp_path: Path):
    return WorkspaceInspection(root_path=str(tmp_path), detected_files=paths, detected_directories=[], detected_languages=["Python"], detected_frameworks_tools=[], important_files=paths, missing_recommended_files=[], warnings=[])


def test_missing_screenshots_block_ready_for_review(tmp_path: Path):
    brief = extract_assignment_brief(BRIEF)
    report = build_final_readiness_report(brief, assignment_number=1, dataset_profile=_profile(tmp_path), workspace_inspection=_workspace(["producer.py", "consumer_to_influx.py", "docker-compose.yml"], tmp_path))
    assert report.readiness_level != "ready_for_review"
    assert report.missing_screenshots


def test_missing_dataset_lowers_readiness(tmp_path: Path):
    brief = extract_assignment_brief(BRIEF)
    report = build_final_readiness_report(brief, assignment_number=1, workspace_inspection=_workspace([], tmp_path))
    assert report.readiness_level in {"not_started", "in_progress"}
    assert report.dataset_risks


def test_missing_report_answers_lower_readiness(tmp_path: Path):
    brief = extract_assignment_brief(BRIEF)
    evidence = build_evidence_checklist(brief)
    for item in list(evidence.items):
        if item.evidence_type != "report_answer":
            evidence = update_evidence_status(evidence, item.evidence_id, "verified")
    report = build_final_readiness_report(brief, assignment_number=1, dataset_profile=_profile(tmp_path), workspace_inspection=_workspace(["producer.py", "consumer_to_influx.py", "docker-compose.yml"], tmp_path), evidence=evidence)
    assert report.missing_report_sections
    assert report.readiness_level != "ready_for_review"


def test_complete_fixture_reaches_ready_for_review(tmp_path: Path):
    brief = extract_assignment_brief(BRIEF)
    evidence = build_evidence_checklist(brief)
    for item in list(evidence.items):
        evidence = update_evidence_status(evidence, item.evidence_id, "verified")
    blueprints = generate_code_blueprints(1, dataset_profile=_profile(tmp_path))
    workspace = _workspace([item.file_path for item in blueprints.blueprints], tmp_path)
    report = build_final_readiness_report(brief, assignment_number=1, dataset_profile=_profile(tmp_path), workspace_inspection=workspace, evidence=evidence, code_blueprints=blueprints)
    assert report.readiness_level == "ready_for_review"
    assert "guarantee" in report.advisory_note.lower()


def test_optional_bonus_does_not_block_readiness(tmp_path: Path):
    brief = extract_assignment_brief(BRIEF)
    evidence = build_evidence_checklist(brief)
    for item in list(evidence.items):
        if item.required:
            evidence = update_evidence_status(evidence, item.evidence_id, "verified")
    blueprints = generate_code_blueprints(1, dataset_profile=_profile(tmp_path))
    workspace = _workspace([item.file_path for item in blueprints.blueprints], tmp_path)
    report = build_final_readiness_report(brief, assignment_number=1, dataset_profile=_profile(tmp_path), workspace_inspection=workspace, evidence=evidence, code_blueprints=blueprints)
    assert report.readiness_level == "ready_for_review"


def test_final_readiness_output_is_deterministic(tmp_path: Path):
    brief = extract_assignment_brief(BRIEF)
    profile = _profile(tmp_path)
    first = build_final_readiness_report(brief, assignment_number=1, dataset_profile=profile).model_dump(mode="json")
    second = build_final_readiness_report(brief, assignment_number=1, dataset_profile=profile).model_dump(mode="json")
    assert first == second
