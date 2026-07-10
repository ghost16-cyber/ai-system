from __future__ import annotations

from pathlib import Path

from backend.app.assignments.extractor import extract_assignment_brief
from backend.app.assignments.report_skeleton import generate_report_skeleton
from backend.app.datasets import profile_csv_dataset


BRIEF = """
Portfolio
Assignment 2: PySpark + Snowflake + Streamlit
Task 1 - Load, Clean & Profile [5 marks]
Clean the dataset.
Screenshot required: PySpark terminal schema and data quality summary.
Dataset requirement: use the supplied transaction CSV.
"""


def _profile(tmp_path: Path):
    path = tmp_path / "events.csv"
    path.write_text("event_time,value,site\n2026-01-01,10,A\n", encoding="utf-8")
    return profile_csv_dataset(path, row_count_override=35_000)


def test_report_skeleton_has_required_sections_and_dataset_profile(tmp_path: Path):
    skeleton = generate_report_skeleton(extract_assignment_brief(BRIEF), dataset_profile=_profile(tmp_path))
    titles = [section.title for section in skeleton.sections]

    assert titles == [
        "Introduction",
        "Dataset Description",
        "Methodology",
        "Results",
        "Screenshots/Evidence",
        "Conclusion",
        "References",
    ]
    assert "Estimated rows: 35000" in skeleton.markdown
    assert "Add only real outputs" in skeleton.markdown


def test_report_skeleton_does_not_invent_results():
    skeleton = generate_report_skeleton(extract_assignment_brief(BRIEF))
    lowered = skeleton.markdown.lower()

    assert "99%" not in lowered
    assert "achieved" not in lowered
    assert skeleton.warnings


def test_report_skeleton_output_is_deterministic(tmp_path: Path):
    brief = extract_assignment_brief(BRIEF)
    profile = _profile(tmp_path)

    first = generate_report_skeleton(brief, dataset_profile=profile).model_dump(mode="json")
    second = generate_report_skeleton(brief, dataset_profile=profile).model_dump(mode="json")

    assert first == second
