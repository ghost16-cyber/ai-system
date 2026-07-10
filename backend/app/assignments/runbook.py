from __future__ import annotations

from pathlib import Path

from backend.app.assignments.schemas import AssignmentRunbook, AssignmentRunbookStep
from backend.app.commands import suggest_command


def generate_assignment_runbook(
    assignment_number: int,
    *,
    workspace_root: str | Path = ".",
) -> AssignmentRunbook:
    root = Path(workspace_root).expanduser().resolve()
    if assignment_number == 1:
        return AssignmentRunbook(assignment_number=1, title="Assignment 1 Runbook: Kafka + InfluxDB + Grafana", steps=_assignment_one(root))
    if assignment_number == 2:
        return AssignmentRunbook(assignment_number=2, title="Assignment 2 Runbook: PySpark + Snowflake + Streamlit", steps=_assignment_two(root))
    if assignment_number == 3:
        return AssignmentRunbook(assignment_number=3, title="Assignment 3 Runbook: Kafka + PySpark Streaming + Redis + Streamlit", steps=_assignment_three(root))
    raise ValueError("Assignment number must be 1, 2, or 3.")


def _step(
    step_id: str,
    title: str,
    explanation: str,
    expected: str,
    hint: str,
    *,
    command=None,
    screenshot: str | None = None,
) -> AssignmentRunbookStep:
    return AssignmentRunbookStep(
        step_id=step_id,
        title=title,
        explanation=explanation,
        command_suggestion=command.model_dump(mode="json") if command else None,
        expected_result=expected,
        screenshot_to_take=screenshot,
        troubleshooting_hint=hint,
    )


def _assignment_one(root: Path) -> list[AssignmentRunbookStep]:
    return [
        _step("a1-01", "Start Docker services", "Start Kafka, InfluxDB, and Grafana from docker-compose.yml.", "Containers show as running.", "If Docker is unavailable, start Docker Desktop first.", command=suggest_command("docker_compose_up", root), screenshot="Docker containers running"),
        _step("a1-02", "Run Kafka producer", "Send sample records into the Kafka topic.", "Producer terminal prints sent events.", "Check bootstrap server and topic name.", command=suggest_command("python_script", root, target="producer.py"), screenshot="Producer terminal"),
        _step("a1-03", "Run consumer to InfluxDB", "Consume Kafka events and write them to InfluxDB.", "Consumer terminal prints processed events.", "Check InfluxDB URL, token placeholders, and bucket.", command=suggest_command("python_script", root, target="consumer_to_influx.py"), screenshot="Consumer terminal"),
        _step("a1-04", "Check InfluxDB Data Explorer", "Open InfluxDB and verify records arrived.", "Recent measurements are visible.", "Check the bucket and time range.", screenshot="InfluxDB Data Explorer"),
        _step("a1-05", "Check Grafana dashboard", "Open Grafana and confirm live panels show data.", "Dashboard panels refresh with recent values.", "Check data source configuration.", screenshot="Grafana dashboard"),
        _step("a1-06", "Stop services safely", "Stop containers when evidence has been captured.", "Services are stopped without deleting project files.", "Avoid destructive volume deletion unless you intentionally want a reset."),
    ]


def _assignment_two(root: Path) -> list[AssignmentRunbookStep]:
    return [
        _step("a2-01", "Run PySpark processing", "Clean/profile the CSV and prepare output for Snowflake.", "PySpark terminal prints schema and preview rows.", "Check dataset path and column names.", command=suggest_command("python_script", root, target="spark_processing.py"), screenshot="PySpark terminal"),
        _step("a2-02", "Load Snowflake output", "Run the loader using placeholder-safe environment configuration.", "Loader reaches Snowflake without exposing secrets.", "Use external browser auth or environment variables; do not paste secrets into code.", command=suggest_command("python_script", root, target="snowflake_loader.py"), screenshot="Snowflake loader terminal"),
        _step("a2-03", "Validate Snowflake worksheet", "Run SQL validation queries in the Snowflake web UI.", "Worksheet shows expected row counts and aggregates.", "Check database/schema/warehouse selection.", screenshot="Snowflake worksheet"),
        _step("a2-04", "Start Streamlit dashboard", "Launch the local dashboard for prepared outputs.", "Browser shows the Streamlit dashboard.", "If port 8501 is busy, choose another port manually.", command=suggest_command("streamlit", root, target="dashboard/app.py"), screenshot="Streamlit dashboard"),
    ]


def _assignment_three(root: Path) -> list[AssignmentRunbookStep]:
    return [
        _step("a3-01", "Start Kafka and Redis", "Start local streaming services.", "Kafka and Redis containers are running.", "Check Docker before retrying.", command=suggest_command("docker_compose_up", root), screenshot="Docker containers running"),
        _step("a3-02", "Run Kafka replay producer", "Replay dataset events into Kafka.", "Replay terminal prints sent events.", "Check data/events.jsonl exists.", command=suggest_command("python_script", root, target="replay_producer.py"), screenshot="Kafka replay terminal"),
        _step("a3-03", "Run Structured Streaming job", "Process Kafka events with PySpark Structured Streaming and watermarking.", "Streaming job prints or writes windowed results.", "Check Kafka package dependencies and schema fields.", command=suggest_command("python_script", root, target="structured_streaming_job.py"), screenshot="Watermark/query plan/logs"),
        _step("a3-04", "Check Redis output", "Verify latest streaming results are written to Redis.", "Redis CLI shows current keys or hashes.", "Check Redis host/port and key names.", screenshot="Redis CLI"),
        _step("a3-05", "Start Streamlit dashboard", "Launch a live dashboard over Redis-backed metrics.", "Dashboard updates from Redis values.", "Confirm Redis helper returns data.", command=suggest_command("streamlit", root, target="dashboard/app.py"), screenshot="Streamlit dashboard"),
    ]
