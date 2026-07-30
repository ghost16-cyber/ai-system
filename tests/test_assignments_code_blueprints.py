from __future__ import annotations

from pathlib import Path

from backend.app.assignments.code_blueprints import generate_code_blueprints
from backend.app.datasets import profile_csv_dataset


def _profile(tmp_path: Path):
    path = tmp_path / "events.csv"
    path.write_text("event_time,temp,humidity,site,label\n2026-01-01,21.5,44,A,1\n", encoding="utf-8")
    return profile_csv_dataset(path, row_count_override=35_000)


def test_assignment_1_blueprint_includes_kafka_and_influxdb(tmp_path: Path):
    result = generate_code_blueprints(1, dataset_profile=_profile(tmp_path))
    paths = {item.file_path for item in result.blueprints}
    assert "producer.py" in paths
    assert "consumer_to_influx.py" in paths
    assert "docker-compose.yml" in paths


def test_assignment_2_blueprint_includes_pyspark_snowflake_streamlit(tmp_path: Path):
    result = generate_code_blueprints(2, dataset_profile=_profile(tmp_path))
    text = "\n".join(item.generated_content for item in result.blueprints)
    assert "SparkSession" in text
    assert "snowflake.connector" in text
    assert "streamlit" in text


def test_assignment_3_blueprint_includes_streaming_redis_watermark(tmp_path: Path):
    result = generate_code_blueprints(3, dataset_profile=_profile(tmp_path))
    text = "\n".join(item.generated_content for item in result.blueprints)
    assert "readStream" in text
    assert "redis.Redis" in text
    assert "withWatermark" in text


def test_dataset_profile_columns_are_inserted_safely(tmp_path: Path):
    result = generate_code_blueprints(2, dataset_profile=_profile(tmp_path))
    text = "\n".join(item.generated_content for item in result.blueprints)
    assert "event_time" in text
    assert "temp" in text
    assert "site" in text


def test_no_credentials_are_generated(tmp_path: Path):
    text = "\n".join(item.generated_content for item in generate_code_blueprints(2, dataset_profile=_profile(tmp_path)).blueprints)
    assert "password" not in text.lower()
    assert "SNOWFLAKE_ACCOUNT" in text
    assert "real" not in text.lower()


def test_blueprint_output_is_deterministic(tmp_path: Path):
    profile = _profile(tmp_path)
    assert generate_code_blueprints(3, dataset_profile=profile).model_dump(mode="json") == generate_code_blueprints(3, dataset_profile=profile).model_dump(mode="json")


def test_generated_code_contains_imports_and_main_functions(tmp_path: Path):
    result = generate_code_blueprints(1, dataset_profile=_profile(tmp_path))
    for item in result.blueprints:
        if item.file_path.endswith(".py"):
            assert "import " in item.generated_content
            assert "def main" in item.generated_content
