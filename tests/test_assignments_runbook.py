from __future__ import annotations

from backend.app.assignments.runbook import generate_assignment_runbook


def test_assignment_1_runbook_includes_docker_producer_consumer_influxdb_grafana(tmp_path):
    runbook = generate_assignment_runbook(1, workspace_root=tmp_path)
    text = " ".join(step.title + " " + step.explanation for step in runbook.steps)
    assert "Docker" in text
    assert "producer" in text
    assert "consumer" in text
    assert "InfluxDB" in text
    assert "Grafana" in text


def test_assignment_2_runbook_includes_pyspark_snowflake_streamlit(tmp_path):
    runbook = generate_assignment_runbook(2, workspace_root=tmp_path)
    text = " ".join(step.title + " " + step.explanation for step in runbook.steps)
    assert "PySpark" in text
    assert "Snowflake" in text
    assert "Streamlit" in text


def test_assignment_3_runbook_includes_streaming_redis_watermark(tmp_path):
    runbook = generate_assignment_runbook(3, workspace_root=tmp_path)
    text = " ".join((step.title + " " + step.explanation + " " + (step.screenshot_to_take or "")) for step in runbook.steps)
    assert "Kafka replay" in text
    assert "Structured Streaming" in text
    assert "Redis" in text
    assert "Watermark" in text


def test_runbook_screenshot_steps_and_commands_are_suggestions_only(tmp_path):
    runbook = generate_assignment_runbook(1, workspace_root=tmp_path)
    assert any(step.screenshot_to_take for step in runbook.steps)
    commands = [step.command_suggestion for step in runbook.steps if step.command_suggestion]
    assert commands
    assert all(command["executed"] is False for command in commands)
    assert runbook.commands_executed is False


def test_runbook_output_is_deterministic(tmp_path):
    first = generate_assignment_runbook(3, workspace_root=tmp_path).model_dump(mode="json")
    second = generate_assignment_runbook(3, workspace_root=tmp_path).model_dump(mode="json")
    assert first == second
