from __future__ import annotations

from pathlib import Path

from backend.app.assignments.analysis_planner import generate_analysis_plan
from backend.app.datasets import profile_csv_dataset


def _profile(tmp_path: Path):
    path = tmp_path / "events.csv"
    path.write_text("event_time,value,region,label\n2026-01-01,10,north,1\n", encoding="utf-8")
    return profile_csv_dataset(path, row_count_override=35_000)


def test_assignment_2_produces_four_questions(tmp_path: Path):
    plan = generate_analysis_plan(2, dataset_profile=_profile(tmp_path))
    assert len(plan.questions) == 4


def test_assignment_2_has_two_dataframe_and_two_sql_questions(tmp_path: Path):
    plan = generate_analysis_plan(2, dataset_profile=_profile(tmp_path))
    methods = [question.method for question in plan.questions]
    assert methods.count("DataFrame API") == 2
    assert methods.count("Spark SQL") == 2


def test_assignment_3_includes_watermark_analysis(tmp_path: Path):
    text = " ".join(question.question + question.suggested_logic for question in generate_analysis_plan(3, dataset_profile=_profile(tmp_path)).questions)
    assert "watermark" in text.lower()


def test_missing_dataset_profile_uses_placeholders():
    plan = generate_analysis_plan(2)
    text = " ".join(question.suggested_logic for question in plan.questions)
    assert "TIMESTAMP_COLUMN" in text
    assert "NUMERIC_COLUMN" in text
    assert plan.warnings


def test_analysis_plan_is_deterministic(tmp_path: Path):
    profile = _profile(tmp_path)
    assert generate_analysis_plan(2, dataset_profile=profile).model_dump(mode="json") == generate_analysis_plan(2, dataset_profile=profile).model_dump(mode="json")
