from __future__ import annotations

from pathlib import Path

from backend.app.debugging import analyze_error_output


def test_import_error_analysis(tmp_path: Path):
    result = analyze_error_output("ModuleNotFoundError: No module named 'kafka'", project_root=tmp_path)

    assert result.error_type == "missing_package"
    assert "kafka" in result.suggested_fix
    assert "requirements.txt" in result.files_to_check
    assert all(command.executed is False for command in result.safe_commands_to_try)


def test_file_not_found_analysis(tmp_path: Path):
    result = analyze_error_output("FileNotFoundError: [Errno 2] No such file or directory: 'data/input.csv'", project_root=tmp_path)

    assert result.error_type == "file_not_found"
    assert result.confidence >= 0.8
    assert "data/" in result.files_to_check


def test_docker_connection_issue_analysis(tmp_path: Path):
    result = analyze_error_output("Cannot connect to the Docker daemon. Is the docker daemon running?", project_root=tmp_path)

    assert result.error_type == "docker_service_not_running"
    assert result.safe_commands_to_try[0].command == "docker ps"


def test_kafka_connection_refused_analysis(tmp_path: Path):
    result = analyze_error_output("kafka.errors.NoBrokersAvailable: Connection refused localhost:9092", project_root=tmp_path)

    assert result.error_type == "kafka_connection_refused"
    assert any(command.command == "docker compose up" for command in result.safe_commands_to_try)
    assert "producer.py" in result.files_to_check


def test_redis_connection_refused_analysis(tmp_path: Path):
    result = analyze_error_output("redis.exceptions.ConnectionError: Error 111 connecting to localhost:6379. Connection refused.", project_root=tmp_path)

    assert result.error_type == "redis_connection_refused"
    assert "redis_helper.py" in result.files_to_check


def test_snowflake_missing_credentials_analysis(tmp_path: Path):
    result = analyze_error_output("snowflake.connector.errors.DatabaseError: missing account credential for user", project_root=tmp_path)

    assert result.error_type == "snowflake_credential_config_error"
    assert "snowflake_loader.py" in result.files_to_check


def test_port_already_in_use_analysis(tmp_path: Path):
    result = analyze_error_output("OSError: [Errno 98] Address already in use. Port 8501 is already in use.", project_root=tmp_path)

    assert result.error_type == "port_already_in_use"
    assert result.safe_commands_to_try == []


def test_unknown_error_fallback_asks_for_context(tmp_path: Path):
    result = analyze_error_output("something odd happened", project_root=tmp_path)

    assert result.error_type == "unknown"
    assert result.confidence < 0.5
    assert result.missing_context_questions
