from __future__ import annotations

from pathlib import Path

from backend.app.workspace import inspect_workspace


def test_inspecting_temporary_workspace_detects_big_data_files(tmp_path: Path):
    (tmp_path / "docker-compose.yml").write_text("services:\n  kafka:\n  grafana:\n", encoding="utf-8")
    (tmp_path / "producer.py").write_text("from kafka import KafkaProducer\n", encoding="utf-8")
    (tmp_path / "dashboard").mkdir()
    (tmp_path / "dashboard" / "app.py").write_text("import streamlit as st\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# Assignment\n", encoding="utf-8")

    inspection = inspect_workspace(tmp_path)

    assert inspection.root_path == str(tmp_path.resolve())
    assert "docker-compose.yml" in inspection.detected_files
    assert "dashboard/app.py" in inspection.detected_files
    assert "Python" in inspection.detected_languages
    assert "Docker Compose" in inspection.detected_frameworks_tools
    assert "Kafka" in inspection.detected_frameworks_tools
    assert "Streamlit" in inspection.detected_frameworks_tools
    assert "producer.py" in inspection.important_files


def test_workspace_inspector_ignores_secret_files(tmp_path: Path):
    (tmp_path / ".env").write_text("SNOWFLAKE_PASSWORD=real-secret\n", encoding="utf-8")
    (tmp_path / "credentials.json").write_text('{"token":"secret"}', encoding="utf-8")
    (tmp_path / "app.py").write_text("print('safe')\n", encoding="utf-8")

    inspection = inspect_workspace(tmp_path)

    assert ".env" not in inspection.detected_files
    assert "credentials.json" not in inspection.detected_files
    assert "app.py" in inspection.detected_files
    assert inspection.files_skipped >= 2


def test_workspace_inspector_ignores_skipped_folders_and_large_data(tmp_path: Path):
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "package.js").write_text("react", encoding="utf-8")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "python").write_text("binary", encoding="utf-8")
    (tmp_path / "data.csv").write_text("x,y\n1,2\n", encoding="utf-8")
    (tmp_path / "spark_processing.py").write_text("from pyspark.sql import SparkSession\n", encoding="utf-8")

    inspection = inspect_workspace(tmp_path)

    assert "node_modules/package.js" not in inspection.detected_files
    assert ".venv/python" not in inspection.detected_files
    assert "data.csv" not in inspection.detected_files
    assert "spark_processing.py" in inspection.detected_files
    assert "PySpark" in inspection.detected_frameworks_tools


def test_workspace_inspector_detects_docker_compose(tmp_path: Path):
    (tmp_path / "docker-compose.yml").write_text("services:\n  redis:\n", encoding="utf-8")

    inspection = inspect_workspace(tmp_path)

    assert "docker-compose.yml" in inspection.important_files
    assert "Docker Compose" in inspection.detected_frameworks_tools
    assert "Redis" in inspection.detected_frameworks_tools


def test_workspace_inspector_output_is_deterministic(tmp_path: Path):
    (tmp_path / "b.py").write_text("print('b')\n", encoding="utf-8")
    (tmp_path / "a.py").write_text("print('a')\n", encoding="utf-8")

    first = inspect_workspace(tmp_path).model_dump(mode="json")
    second = inspect_workspace(tmp_path).model_dump(mode="json")

    assert first == second
