from __future__ import annotations

from pathlib import Path

from backend.app.assignments.extractor import extract_assignment_brief
from backend.app.assignments.workspace_builder import plan_assignment_workspace
from backend.app.datasets import profile_csv_dataset


BRIEF = """
Portfolio
Assignment 1: Kafka + InfluxDB + Grafana
Task: Build producer and dashboard.
Screenshot required: Docker containers running, Grafana dashboard.
Assignment 2: PySpark + Snowflake + Streamlit
Task: Load Snowflake and build Streamlit.
Screenshot required: Snowflake worksheet, Streamlit dashboard.
Assignment 3: Kafka + PySpark Structured Streaming + Redis + Streamlit
Task: Build streaming job with Redis output.
Screenshot required: Redis CLI, Streamlit dashboard.
"""


def _dataset(tmp_path: Path, rows: int = 5):
    path = tmp_path / "events.csv"
    path.write_text("timestamp,value,category\n" + "\n".join(f"2026-01-01,{i},a" for i in range(rows)), encoding="utf-8")
    return profile_csv_dataset(path, row_count_override=35_000)


def test_build_plan_includes_correct_files_for_assignment_1(tmp_path: Path):
    plan = plan_assignment_workspace(extract_assignment_brief(BRIEF), assignment_number=1, workspace_root=tmp_path)
    paths = {file.file_path for file in plan.files_to_create}
    assert "producer.py" in paths
    assert "consumer_to_influx.py" in paths
    assert "docker-compose.yml" in paths


def test_build_plan_includes_correct_files_for_assignment_2(tmp_path: Path):
    plan = plan_assignment_workspace(extract_assignment_brief(BRIEF), assignment_number=2, workspace_root=tmp_path)
    paths = {file.file_path for file in plan.files_to_create}
    assert "spark_processing.py" in paths
    assert "snowflake_loader.py" in paths
    assert "config/example.env" in paths


def test_build_plan_includes_correct_files_for_assignment_3(tmp_path: Path):
    plan = plan_assignment_workspace(extract_assignment_brief(BRIEF), assignment_number=3, workspace_root=tmp_path)
    paths = {file.file_path for file in plan.files_to_create}
    assert "structured_streaming_job.py" in paths
    assert "redis_helper.py" in paths


def test_dataset_profile_affects_recommendations(tmp_path: Path):
    plan = plan_assignment_workspace(extract_assignment_brief(BRIEF), assignment_number=2, workspace_root=tmp_path / "work", dataset_profile=_dataset(tmp_path))
    assert "suitable for Assignment 2" in " ".join(plan.risks_warnings)


def test_existing_files_are_skipped_by_default(tmp_path: Path):
    (tmp_path / "producer.py").write_text("keep\n", encoding="utf-8")
    plan = plan_assignment_workspace(extract_assignment_brief(BRIEF), assignment_number=1, workspace_root=tmp_path)
    assert "producer.py" in plan.files_to_skip


def test_no_credentials_are_generated(tmp_path: Path):
    plan = plan_assignment_workspace(extract_assignment_brief(BRIEF), assignment_number=2, workspace_root=tmp_path)
    combined = "\n".join(file.content_preview.lower() for file in plan.files_to_create)
    assert "password=" not in combined
    assert "replace_with" in combined


def test_no_files_written_unless_explicitly_requested(tmp_path: Path):
    plan_assignment_workspace(extract_assignment_brief(BRIEF), assignment_number=1, workspace_root=tmp_path)
    assert not (tmp_path / "producer.py").exists()
    written = plan_assignment_workspace(extract_assignment_brief(BRIEF), assignment_number=1, workspace_root=tmp_path, write_files=True)
    assert written.files_written is True
    assert (tmp_path / "producer.py").exists()
