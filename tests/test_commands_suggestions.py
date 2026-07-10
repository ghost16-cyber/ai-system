from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.commands import analyze_command, suggest_command


def test_safe_command_suggestions_do_not_execute(tmp_path: Path):
    suggestion = suggest_command("pytest", tmp_path)

    assert suggestion.command == "python -m pytest -q"
    assert suggestion.risk_level == "low"
    assert suggestion.requires_confirmation is False
    assert suggestion.allowed is True
    assert suggestion.executed is False


def test_dangerous_command_is_rejected(tmp_path: Path):
    suggestion = analyze_command("rm -rf data", tmp_path, project_root=tmp_path)

    assert suggestion.allowed is False
    assert suggestion.risk_level == "high"
    assert suggestion.requires_confirmation is True
    assert "Rejected" in suggestion.why_safe


def test_docker_and_streamlit_suggestions(tmp_path: Path):
    docker = suggest_command("docker_compose_up", tmp_path)
    streamlit = suggest_command("streamlit", tmp_path, target="dashboard/app.py")

    assert docker.command == "docker compose up"
    assert docker.risk_level == "medium"
    assert docker.requires_confirmation is True
    assert streamlit.command == "streamlit run dashboard/app.py"
    assert streamlit.expected_output_hint
    assert docker.executed is False
    assert streamlit.executed is False


def test_command_workdir_outside_project_is_refused(tmp_path: Path):
    outside = tmp_path.parent

    with pytest.raises(ValueError):
        suggest_command("pytest", tmp_path, working_directory=outside)


def test_command_suggestions_are_deterministic(tmp_path: Path):
    first = suggest_command("python_script", tmp_path, target="producer.py").model_dump(mode="json")
    second = suggest_command("python_script", tmp_path, target="producer.py").model_dump(mode="json")

    assert first == second


def test_safe_suggestion_mode_marks_risky_without_execution(tmp_path: Path):
    suggestion = suggest_command("docker_compose_up", tmp_path)

    assert suggestion.allowed is True
    assert suggestion.risk_level == "medium"
    assert suggestion.requires_confirmation is True
    assert suggestion.executed is False


def test_safe_suggestion_mode_refuses_destructive_commands(tmp_path: Path):
    suggestion = analyze_command("sudo rm -rf /", tmp_path, project_root=tmp_path)

    assert suggestion.allowed is False
    assert suggestion.risk_level == "high"
    assert suggestion.executed is False
    assert "No command should be executed" in suggestion.expected_output_hint
