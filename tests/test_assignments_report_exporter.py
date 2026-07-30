from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.assignments.evidence import build_evidence_checklist
from backend.app.assignments.extractor import extract_assignment_brief
from backend.app.assignments.marking_checker import check_marking_readiness
from backend.app.assignments.report_exporter import export_report_package
from backend.app.assignments.report_generator import generate_report_draft
from backend.app.assignments.runbook import generate_assignment_runbook


BRIEF = """
Portfolio
Assignment 1: Kafka + InfluxDB + Grafana
Task: Build Kafka producer. 10 marks
Screenshot required: Producer terminal, Grafana dashboard.
Analysis question: Explain Kafka ingestion?
"""


def _package_inputs():
    brief = extract_assignment_brief(BRIEF)
    evidence = build_evidence_checklist(brief)
    report = generate_report_draft(brief, evidence=evidence)
    runbook = generate_assignment_runbook(1)
    marking = check_marking_readiness(brief, evidence)
    return report, evidence, runbook, marking


def test_report_exporter_exports_expected_markdown_files(tmp_path: Path):
    report, evidence, runbook, marking = _package_inputs()
    result = export_report_package(tmp_path, report_draft=report, evidence=evidence, runbook=runbook, marking_readiness=marking)

    assert set(Path(path).name for path in result.created_files) == {
        "report_outline.md",
        "evidence_checklist.md",
        "runbook.md",
        "marking_readiness.md",
        "appendix_code_checklist.md",
    }


def test_report_exporter_refuses_unsafe_paths(tmp_path: Path):
    report, evidence, runbook, marking = _package_inputs()
    with pytest.raises(ValueError):
        export_report_package(tmp_path, report_draft=report, evidence=evidence, runbook=runbook, marking_readiness=marking, report_folder="../outside")


def test_report_exporter_does_not_overwrite_by_default(tmp_path: Path):
    report, evidence, runbook, marking = _package_inputs()
    folder = tmp_path / "report_package"
    folder.mkdir()
    (folder / "report_outline.md").write_text("keep\n", encoding="utf-8")
    result = export_report_package(tmp_path, report_draft=report, evidence=evidence, runbook=runbook, marking_readiness=marking)
    assert "report_package/report_outline.md" in result.skipped_files
    assert (folder / "report_outline.md").read_text(encoding="utf-8") == "keep\n"


def test_report_exporter_includes_placeholders_questions_and_marking(tmp_path: Path):
    report, evidence, runbook, marking = _package_inputs()
    export_report_package(tmp_path, report_draft=report, evidence=evidence, runbook=runbook, marking_readiness=marking)
    combined = "\n".join(path.read_text(encoding="utf-8") for path in (tmp_path / "report_package").glob("*.md"))
    assert "MISSING" in combined
    assert "Explain Kafka ingestion" in combined
    assert "Marking Readiness" in combined


def test_report_exporter_output_is_deterministic(tmp_path: Path):
    report, evidence, runbook, marking = _package_inputs()
    first = export_report_package(tmp_path / "one", report_draft=report, evidence=evidence, runbook=runbook, marking_readiness=marking).model_dump(mode="json")
    second = export_report_package(tmp_path / "two", report_draft=report, evidence=evidence, runbook=runbook, marking_readiness=marking).model_dump(mode="json")
    assert [Path(path).name for path in first["created_files"]] == [Path(path).name for path in second["created_files"]]
