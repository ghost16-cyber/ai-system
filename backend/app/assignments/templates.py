from __future__ import annotations

from pathlib import Path

from backend.app.assignments.schemas import (
    AssignmentTemplateFile,
    AssignmentTemplatePlan,
    AssignmentTemplateWriteResult,
)


def generate_assignment_template_plan(assignment_number: int) -> AssignmentTemplatePlan:
    if assignment_number == 1:
        return AssignmentTemplatePlan(
            assignment_number=1,
            assignment_name="Kafka + InfluxDB + Grafana",
            files=_assignment_one_files(),
        )
    if assignment_number == 2:
        return AssignmentTemplatePlan(
            assignment_number=2,
            assignment_name="PySpark + Snowflake + Streamlit",
            files=_assignment_two_files(),
        )
    if assignment_number == 3:
        return AssignmentTemplatePlan(
            assignment_number=3,
            assignment_name="Kafka + PySpark Structured Streaming + Redis + Streamlit",
            files=_assignment_three_files(),
        )
    raise ValueError("Assignment number must be 1, 2, or 3.")


def write_assignment_template_plan(
    workspace_root: str | Path,
    plan: AssignmentTemplatePlan,
    *,
    overwrite: bool = False,
) -> AssignmentTemplateWriteResult:
    root = Path(workspace_root).expanduser().resolve()
    if not root.exists():
        root.mkdir(parents=True)
    if not root.is_dir():
        raise ValueError("Template workspace root must be a directory.")

    created: list[str] = []
    skipped: list[str] = []
    refused: list[str] = []
    for file_plan in plan.files:
        try:
            target = _safe_target(root, file_plan.file_path)
        except ValueError:
            refused.append(file_plan.file_path)
            continue
        if not file_plan.safe_to_create:
            refused.append(file_plan.file_path)
            continue
        relative = target.relative_to(root).as_posix()
        if target.exists() and not overwrite:
            skipped.append(relative)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(file_plan.content_preview, encoding="utf-8")
        created.append(relative)
    return AssignmentTemplateWriteResult(
        workspace_root=str(root),
        created_files=created,
        skipped_files=skipped,
        refused_files=refused,
        overwrite=overwrite,
    )


def _file(
    assignment_number: int,
    file_path: str,
    purpose: str,
    content: str,
    technology_area: str,
) -> AssignmentTemplateFile:
    return AssignmentTemplateFile(
        file_path=file_path,
        purpose=purpose,
        content_preview=content.strip() + "\n",
        technology_area=technology_area,
        assignment_number=assignment_number,
        safe_to_create=not _unsafe_template_path(file_path),
    )


def _assignment_one_files() -> list[AssignmentTemplateFile]:
    return [
        _file(1, "docker-compose.yml", "Local Kafka, InfluxDB, and Grafana services.", _compose_assignment_one(), "Docker/Kafka/InfluxDB/Grafana"),
        _file(1, "producer.py", "Example Kafka producer for replaying sensor events.", _producer_py(), "Kafka"),
        _file(1, "consumer_to_influx.py", "Kafka consumer that writes parsed events to InfluxDB.", _consumer_to_influx_py(), "Kafka/InfluxDB"),
        _file(1, "requirements.txt", "Python dependencies for Assignment 1.", "kafka-python\ninfluxdb-client\npython-dotenv\n", "Python"),
        _file(1, "README.md", "Setup and run notes for Assignment 1.", _readme(1, "Kafka + InfluxDB + Grafana"), "Documentation"),
        _file(1, "report_outline.md", "Report structure with screenshot checklist and analysis prompts.", _report_outline(1, "Kafka + InfluxDB + Grafana"), "Report"),
    ]


def _assignment_two_files() -> list[AssignmentTemplateFile]:
    return [
        _file(2, "spark_processing.py", "PySpark ETL starter job.", _spark_processing_py(), "PySpark"),
        _file(2, "snowflake_loader.py", "Snowflake loading stub using environment variables.", _snowflake_loader_py(), "Snowflake"),
        _file(2, "dashboard/app.py", "Streamlit dashboard starter.", _streamlit_app_py("Snowflake metrics dashboard"), "Streamlit"),
        _file(2, "config/example.env", "Placeholder config example without real credentials.", _snowflake_example_env(), "Configuration"),
        _file(2, "requirements.txt", "Python dependencies for Assignment 2.", "pyspark\nsnowflake-connector-python\nstreamlit\npython-dotenv\npandas\n", "Python"),
        _file(2, "README.md", "Setup and run notes for Assignment 2.", _readme(2, "PySpark + Snowflake + Streamlit"), "Documentation"),
        _file(2, "report_outline.md", "Report structure with screenshot checklist and analysis prompts.", _report_outline(2, "PySpark + Snowflake + Streamlit"), "Report"),
    ]


def _assignment_three_files() -> list[AssignmentTemplateFile]:
    return [
        _file(3, "docker-compose.yml", "Local Kafka and Redis services for streaming.", _compose_assignment_three(), "Docker/Kafka/Redis"),
        _file(3, "replay_producer.py", "Kafka replay producer for streaming input events.", _replay_producer_py(), "Kafka"),
        _file(3, "structured_streaming_job.py", "PySpark Structured Streaming starter job.", _structured_streaming_job_py(), "PySpark Structured Streaming"),
        _file(3, "redis_helper.py", "Redis helper for dashboard reads.", _redis_helper_py(), "Redis"),
        _file(3, "dashboard/app.py", "Streamlit dashboard starter for Redis-backed results.", _streamlit_app_py("Streaming Redis dashboard"), "Streamlit"),
        _file(3, "requirements.txt", "Python dependencies for Assignment 3.", "kafka-python\npyspark\nredis\nstreamlit\npandas\n", "Python"),
        _file(3, "README.md", "Setup and run notes for Assignment 3.", _readme(3, "Kafka + PySpark Structured Streaming + Redis + Streamlit"), "Documentation"),
        _file(3, "report_outline.md", "Report structure with screenshot checklist and analysis prompts.", _report_outline(3, "Kafka + PySpark Structured Streaming + Redis + Streamlit"), "Report"),
    ]


def _safe_target(root: Path, file_path: str) -> Path:
    if _unsafe_template_path(file_path):
        raise ValueError("Template file path must be relative and stay inside the workspace.")
    target = (root / file_path).resolve()
    try:
        target.relative_to(root)
    except ValueError as error:
        raise ValueError("Template file path must stay inside the workspace.") from error
    return target


def _unsafe_template_path(file_path: str) -> bool:
    path = Path(file_path)
    return path.is_absolute() or ".." in path.parts


def _compose_assignment_one() -> str:
    return """
services:
  zookeeper:
    image: confluentinc/cp-zookeeper:7.6.0
    environment:
      ZOOKEEPER_CLIENT_PORT: 2181
  kafka:
    image: confluentinc/cp-kafka:7.6.0
    depends_on: [zookeeper]
    ports: ["9092:9092"]
    environment:
      KAFKA_BROKER_ID: 1
      KAFKA_ZOOKEEPER_CONNECT: zookeeper:2181
      KAFKA_ADVERTISED_LISTENERS: PLAINTEXT://localhost:9092
      KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR: 1
  influxdb:
    image: influxdb:2
    ports: ["8086:8086"]
  grafana:
    image: grafana/grafana:latest
    ports: ["3000:3000"]
"""


def _compose_assignment_three() -> str:
    return """
services:
  zookeeper:
    image: confluentinc/cp-zookeeper:7.6.0
    environment:
      ZOOKEEPER_CLIENT_PORT: 2181
  kafka:
    image: confluentinc/cp-kafka:7.6.0
    depends_on: [zookeeper]
    ports: ["9092:9092"]
    environment:
      KAFKA_BROKER_ID: 1
      KAFKA_ZOOKEEPER_CONNECT: zookeeper:2181
      KAFKA_ADVERTISED_LISTENERS: PLAINTEXT://localhost:9092
      KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR: 1
  redis:
    image: redis:7
    ports: ["6379:6379"]
"""


def _producer_py() -> str:
    return """
import json
import time
from kafka import KafkaProducer

producer = KafkaProducer(
    bootstrap_servers="localhost:9092",
    value_serializer=lambda value: json.dumps(value).encode("utf-8"),
)

for index in range(5):
    event = {"sensor_id": "demo-1", "temperature": 20 + index, "sequence": index}
    producer.send("sensor-events", event)
    print(f"sent {event}")
    time.sleep(1)
producer.flush()
"""


def _consumer_to_influx_py() -> str:
    return """
from kafka import KafkaConsumer

# TODO: configure InfluxDB using local environment variables.
consumer = KafkaConsumer("sensor-events", bootstrap_servers="localhost:9092")
for message in consumer:
    print("write this event to InfluxDB:", message.value)
"""


def _spark_processing_py() -> str:
    return """
from pyspark.sql import SparkSession

spark = SparkSession.builder.appName("assignment-2-etl").getOrCreate()
input_path = "data/input"
df = spark.read.option("header", True).csv(input_path)
df.printSchema()
df.show(5)
"""


def _snowflake_loader_py() -> str:
    return """
import os

import snowflake.connector

connection_kwargs = {
    "account": os.getenv("SNOWFLAKE_ACCOUNT"),
    "user": os.getenv("SNOWFLAKE_USER"),
    "warehouse": os.getenv("SNOWFLAKE_WAREHOUSE"),
    "database": os.getenv("SNOWFLAKE_DATABASE"),
    "schema": os.getenv("SNOWFLAKE_SCHEMA"),
    "authenticator": os.getenv("SNOWFLAKE_AUTHENTICATOR", "externalbrowser"),
}
print("Ready to connect to Snowflake with placeholder configuration.")
"""


def _snowflake_example_env() -> str:
    return """
SNOWFLAKE_ACCOUNT=replace_with_account
SNOWFLAKE_USER=replace_with_user
SNOWFLAKE_WAREHOUSE=replace_with_warehouse
SNOWFLAKE_DATABASE=replace_with_database
SNOWFLAKE_SCHEMA=replace_with_schema
SNOWFLAKE_AUTHENTICATOR=externalbrowser
"""


def _streamlit_app_py(title: str) -> str:
    return f"""
import streamlit as st

st.set_page_config(page_title="{title}")
st.title("{title}")
st.write("Replace this starter view with assignment metrics and screenshots.")
"""


def _replay_producer_py() -> str:
    return """
import json
from pathlib import Path

from kafka import KafkaProducer

producer = KafkaProducer(
    bootstrap_servers="localhost:9092",
    value_serializer=lambda value: json.dumps(value).encode("utf-8"),
)
for line in Path("data/events.jsonl").read_text(encoding="utf-8").splitlines():
    producer.send("streaming-events", json.loads(line))
producer.flush()
"""


def _structured_streaming_job_py() -> str:
    return """
from pyspark.sql import SparkSession
from pyspark.sql.functions import col, from_json
from pyspark.sql.types import StringType, StructField, StructType

spark = SparkSession.builder.appName("assignment-3-streaming").getOrCreate()
schema = StructType([StructField("sensor_id", StringType()), StructField("value", StringType())])
raw = spark.readStream.format("kafka").option("kafka.bootstrap.servers", "localhost:9092").option("subscribe", "streaming-events").load()
parsed = raw.select(from_json(col("value").cast("string"), schema).alias("event")).select("event.*")
query = parsed.writeStream.format("console").outputMode("append").start()
query.awaitTermination()
"""


def _redis_helper_py() -> str:
    return """
import redis

client = redis.Redis(host="localhost", port=6379, decode_responses=True)

def get_latest_metrics():
    return client.hgetall("latest_metrics")
"""


def _readme(assignment_number: int, title: str) -> str:
    return f"""
# Assignment {assignment_number}: {title}

This starter is a deterministic scaffold. Review every file, fill in dataset paths, and run commands manually.

## Suggested order
1. Install dependencies.
2. Start required local services.
3. Run the pipeline scripts.
4. Capture screenshots.
5. Complete the report outline.
"""


def _report_outline(assignment_number: int, title: str) -> str:
    return f"""
# Assignment {assignment_number} Report: {title}

## Implementation summary
- Describe the architecture and data flow.

## Screenshot checklist
- Service startup or connection evidence.
- Pipeline or processing output.
- Dashboard view with relevant metrics.

## Analysis questions
- Explain why the selected tools fit this workload.
- Discuss data quality, reliability, and performance tradeoffs.

## Marking checklist
- Confirm every required output is present.
- Link screenshots to the implementation sections.
"""
