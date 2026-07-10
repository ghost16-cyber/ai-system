from __future__ import annotations

import re
from pathlib import Path

from backend.app.commands import CommandSuggestion, suggest_command
from backend.app.debugging.schemas import ErrorAnalysis


def analyze_error_output(
    output: str,
    *,
    project_root: str | Path = ".",
) -> ErrorAnalysis:
    text = output or ""
    lowered = text.lower()
    root = Path(project_root).expanduser().resolve()

    if not text.strip():
        return _unknown(root, ["Paste the full terminal output, including the command you ran."])
    if "modulenotfounderror" in lowered or "importerror" in lowered:
        package = _missing_package(text)
        files = ["requirements.txt", "pyproject.toml"]
        if package:
            fix = f"Add or install the missing package `{package}` in the active environment, then rerun the same command."
        else:
            fix = "Check the import name and project dependencies, then rerun the same command."
        return _analysis("missing_package", "Python could not import a required module.", fix, [_pytest(root)], files, 0.88)
    if "filenotfounderror" in lowered or "no such file or directory" in lowered:
        return _analysis(
            "file_not_found",
            "The command references a file path that does not exist from the current working directory.",
            "Verify the relative path, dataset location, and working directory before rerunning.",
            [suggest_command("list_files", root)],
            ["README.md", "data/", "config/"],
            0.84,
        )
    if "cannot connect to the docker daemon" in lowered or "is the docker daemon running" in lowered:
        return _analysis(
            "docker_service_not_running",
            "Docker is not reachable, so local containers cannot be inspected or started.",
            "Start Docker Desktop or the Docker service, then check container status.",
            [suggest_command("docker_ps", root)],
            ["docker-compose.yml"],
            0.92,
        )
    if "address already in use" in lowered or re.search(r"port\s+\d+.*already in use", lowered):
        return _analysis(
            "port_already_in_use",
            "Another local process is already using the requested port.",
            "Choose another port or stop the process that is occupying the port.",
            [],
            ["docker-compose.yml", "dashboard/app.py"],
            0.86,
        )
    if "snowflake" in lowered and any(term in lowered for term in ("credential", "password", "auth", "account", "user")):
        return _analysis(
            "snowflake_credential_config_error",
            "Snowflake connection settings are missing or not valid for the selected authentication method.",
            "Check placeholder config values and prefer environment variables or external browser auth for local work.",
            [],
            ["config/example.env", ".env", "snowflake_loader.py"],
            0.87,
        )
    if ("kafka" in lowered or "nobrokersavailable" in lowered) and _has_connection_refused(lowered):
        return _analysis(
            "kafka_connection_refused",
            "The Kafka client cannot reach a broker on the configured host and port.",
            "Confirm Kafka is running and that the bootstrap server matches docker-compose.yml.",
            [suggest_command("docker_ps", root), suggest_command("docker_compose_up", root)],
            ["docker-compose.yml", "producer.py", "consumer_to_influx.py", "structured_streaming_job.py"],
            0.9,
        )
    if "redis" in lowered and _has_connection_refused(lowered):
        return _analysis(
            "redis_connection_refused",
            "The Redis client cannot reach Redis on the configured host and port.",
            "Confirm Redis is running and that the dashboard/helper uses the expected host and port.",
            [suggest_command("docker_ps", root), suggest_command("docker_compose_up", root)],
            ["docker-compose.yml", "redis_helper.py", "dashboard/app.py"],
            0.9,
        )
    if "pyspark" in lowered or ("schema" in lowered and "spark" in lowered):
        return _analysis(
            "pyspark_schema_type_error",
            "Spark rejected a schema, column, or type conversion used by the job.",
            "Inspect the input schema and cast columns explicitly before transformations.",
            [],
            ["spark_processing.py", "structured_streaming_job.py"],
            0.72,
        )
    if "streamlit" in lowered:
        return _analysis(
            "streamlit_startup_error",
            "Streamlit failed during startup or while importing the dashboard app.",
            "Check the dashboard entry file and install dependencies before starting Streamlit again.",
            [suggest_command("streamlit", root)],
            ["dashboard/app.py", "requirements.txt"],
            0.74,
        )
    return _unknown(root, ["Share the command that produced this output.", "Share the relevant file path or assignment step."])


def _analysis(
    error_type: str,
    cause: str,
    fix: str,
    commands: list[CommandSuggestion],
    files: list[str],
    confidence: float,
) -> ErrorAnalysis:
    return ErrorAnalysis(
        error_type=error_type,
        likely_cause=cause,
        suggested_fix=fix,
        safe_commands_to_try=commands,
        files_to_check=files,
        confidence=confidence,
        needs_user_action=True,
    )


def _unknown(root: Path, questions: list[str]) -> ErrorAnalysis:
    return ErrorAnalysis(
        error_type="unknown",
        likely_cause="The pasted output does not include a recognizable deterministic error pattern.",
        suggested_fix="Collect the full command, traceback, and the assignment step before choosing a fix.",
        safe_commands_to_try=[suggest_command("list_files", root)],
        files_to_check=[],
        confidence=0.25,
        needs_user_action=True,
        missing_context_questions=questions,
    )


def _missing_package(text: str) -> str | None:
    patterns = (
        r"No module named ['\"]([^'\"]+)['\"]",
        r"cannot import name ['\"]([^'\"]+)['\"]",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return None


def _has_connection_refused(lowered: str) -> bool:
    return (
        "connection refused" in lowered
        or "failed to establish a new connection" in lowered
        or "nobrokersavailable" in lowered
        or "errno 111" in lowered
    )


def _pytest(root: Path) -> CommandSuggestion:
    return suggest_command("pytest", root)
