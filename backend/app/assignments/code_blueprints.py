from __future__ import annotations

from backend.app.assignments.schemas import (
    AssignmentCodeBlueprint,
    AssignmentCodeBlueprintSet,
)
from backend.app.datasets.schemas import DatasetProfile


def generate_code_blueprints(
    assignment_number: int,
    *,
    dataset_profile: DatasetProfile | None = None,
) -> AssignmentCodeBlueprintSet:
    context = _column_context(dataset_profile)
    if assignment_number == 1:
        blueprints = [
            _blueprint(1, "docker-compose.yml", "Local Kafka, InfluxDB, and Grafana services.", "Docker/Kafka/InfluxDB/Grafana", _compose(), ["Docker containers running"]),
            _blueprint(1, "producer.py", "Read a CSV and publish records to Kafka.", "Kafka", _producer(context), ["Producer terminal"]),
            _blueprint(1, "consumer_to_influx.py", "Consume Kafka records and write numeric fields to InfluxDB.", "Kafka/InfluxDB", _consumer_influx(context), ["Consumer terminal", "InfluxDB Data Explorer", "Grafana dashboard"]),
        ]
    elif assignment_number == 2:
        blueprints = [
            _blueprint(2, "spark_processing.py", "Clean and aggregate the dataset with PySpark.", "PySpark", _spark_processing(context), ["PySpark terminal"]),
            _blueprint(2, "snowflake_loader.py", "Load prepared outputs to Snowflake using environment variables.", "Snowflake", _snowflake_loader(), ["Snowflake worksheet"]),
            _blueprint(2, "dashboard/app.py", "Streamlit dashboard over Snowflake-style aggregate outputs.", "Streamlit", _streamlit_dashboard(context), ["Streamlit dashboard"]),
        ]
    elif assignment_number == 3:
        blueprints = [
            _blueprint(3, "replay_producer.py", "Replay CSV rows to Kafka for streaming.", "Kafka", _replay_producer(context), ["Kafka replay terminal"]),
            _blueprint(3, "structured_streaming_job.py", "Process Kafka events with watermarking and write summaries to Redis.", "PySpark Structured Streaming/Redis", _structured_streaming(context), ["Watermark/query plan/logs", "Redis CLI"]),
            _blueprint(3, "redis_helper.py", "Read and write live dashboard metrics in Redis.", "Redis", _redis_helper(), ["Redis CLI"]),
            _blueprint(3, "dashboard/app.py", "Live Streamlit dashboard backed by Redis.", "Streamlit/Redis", _live_dashboard(), ["Streamlit dashboard"]),
        ]
    else:
        raise ValueError("Assignment number must be 1, 2, or 3.")
    return AssignmentCodeBlueprintSet(
        assignment_number=assignment_number,
        blueprints=blueprints,
        warnings=[] if dataset_profile else ["Dataset profile missing; generated code uses clearly marked column placeholders."],
    )


def _blueprint(assignment: int, path: str, purpose: str, tech: str, content: str, screenshots: list[str]) -> AssignmentCodeBlueprint:
    return AssignmentCodeBlueprint(
        file_path=path,
        purpose=purpose,
        assignment_number=assignment,
        technology_area=tech,
        required_inputs=_required_inputs(content),
        generated_content=content.strip() + "\n",
        placeholders=[token for token in ("DATASET_PATH", "TIMESTAMP_COLUMN", "NUMERIC_COLUMN", "CATEGORY_COLUMN", "CLASSIFICATION_COLUMN") if token in content],
        safety_notes=[
            "Review before running; Astra does not execute generated code.",
            "No real credentials are embedded.",
            "Replace placeholders with verified local values.",
        ],
        expected_screenshot_links=screenshots,
    )


def _column_context(profile: DatasetProfile | None) -> dict[str, str]:
    date = profile.detected_date_columns[0] if profile and profile.detected_date_columns else "TIMESTAMP_COLUMN"
    numeric = profile.detected_numeric_columns[0] if profile and profile.detected_numeric_columns else "NUMERIC_COLUMN"
    numeric_two = profile.detected_numeric_columns[1] if profile and len(profile.detected_numeric_columns) > 1 else numeric
    category = profile.detected_categorical_columns[0] if profile and profile.detected_categorical_columns else "CATEGORY_COLUMN"
    classification = _classification_column(profile) if profile else "CLASSIFICATION_COLUMN"
    return {
        "date": date,
        "numeric": numeric,
        "numeric_two": numeric_two,
        "category": category,
        "classification": classification,
        "dataset": profile.dataset_path if profile else "DATASET_PATH",
    }


def _classification_column(profile: DatasetProfile | None) -> str:
    if not profile:
        return "CLASSIFICATION_COLUMN"
    for column in profile.detected_numeric_columns:
        if column.lower() in {"label", "target", "severity", "class", "indicator"}:
            return column
    return profile.detected_numeric_columns[0] if profile.detected_numeric_columns else "CLASSIFICATION_COLUMN"


def _required_inputs(content: str) -> list[str]:
    inputs = []
    if "DATASET_PATH" in content:
        inputs.append("DATASET_PATH")
    if "os.getenv" in content:
        inputs.append("Environment variables")
    if "localhost:9092" in content:
        inputs.append("Kafka bootstrap server")
    if "localhost:6379" in content:
        inputs.append("Redis server")
    return inputs


def _compose() -> str:
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


def _producer(ctx: dict[str, str]) -> str:
    return f'''
import csv
import json
import os
import time
from kafka import KafkaProducer

TOPIC = os.getenv("KAFKA_TOPIC", "assignment-events")
DATASET_PATH = os.getenv("DATASET_PATH", "{ctx["dataset"]}")

def build_event(row):
    return {{
        "timestamp": row.get("{ctx["date"]}"),
        "category": row.get("{ctx["category"]}"),
        "value": float(row.get("{ctx["numeric"]}", 0) or 0),
        "raw": row,
    }}

def main():
    producer = KafkaProducer(
        bootstrap_servers=os.getenv("KAFKA_BOOTSTRAP", "localhost:9092"),
        value_serializer=lambda value: json.dumps(value).encode("utf-8"),
    )
    with open(DATASET_PATH, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            producer.send(TOPIC, build_event(row))
            time.sleep(float(os.getenv("REPLAY_DELAY_SECONDS", "0.1")))
    producer.flush()

if __name__ == "__main__":
    main()
'''


def _consumer_influx(ctx: dict[str, str]) -> str:
    return f'''
import json
import os
from kafka import KafkaConsumer
from influxdb_client import InfluxDBClient, Point

TOPIC = os.getenv("KAFKA_TOPIC", "assignment-events")

def point_from_event(event):
    return (
        Point("assignment_metric")
        .tag("category", str(event.get("category", "unknown")))
        .field("{ctx["numeric"]}", float(event.get("value", 0) or 0))
    )

def main():
    consumer = KafkaConsumer(TOPIC, bootstrap_servers=os.getenv("KAFKA_BOOTSTRAP", "localhost:9092"))
    with InfluxDBClient(
        url=os.getenv("INFLUXDB_URL", "http://localhost:8086"),
        token=os.getenv("INFLUXDB_TOKEN", "REPLACE_WITH_LOCAL_TOKEN"),
        org=os.getenv("INFLUXDB_ORG", "REPLACE_WITH_LOCAL_ORG"),
    ) as client:
        writer = client.write_api()
        bucket = os.getenv("INFLUXDB_BUCKET", "assignment")
        for message in consumer:
            event = json.loads(message.value.decode("utf-8"))
            writer.write(bucket=bucket, record=point_from_event(event))

if __name__ == "__main__":
    main()
'''


def _spark_processing(ctx: dict[str, str]) -> str:
    return f'''
from pyspark.sql import SparkSession
from pyspark.sql.functions import avg, col, count, to_timestamp

DATASET_PATH = "{ctx["dataset"]}"
TIMESTAMP_COLUMN = "{ctx["date"]}"
CATEGORY_COLUMN = "{ctx["category"]}"
NUMERIC_COLUMN = "{ctx["numeric"]}"

def main():
    spark = SparkSession.builder.appName("assignment-2-processing").getOrCreate()
    df = spark.read.option("header", True).option("inferSchema", True).csv(DATASET_PATH)
    cleaned = df.withColumn("event_time", to_timestamp(col(TIMESTAMP_COLUMN))).dropna(subset=[NUMERIC_COLUMN])
    by_category = cleaned.groupBy(CATEGORY_COLUMN).agg(count("*").alias("row_count"), avg(NUMERIC_COLUMN).alias("avg_value"))
    by_category.show(20, truncate=False)
    by_category.write.mode("overwrite").parquet("outputs/category_summary")
    spark.stop()

if __name__ == "__main__":
    main()
'''


def _snowflake_loader() -> str:
    return '''
import os
import snowflake.connector

def connect():
    return snowflake.connector.connect(
        account=os.getenv("SNOWFLAKE_ACCOUNT"),
        user=os.getenv("SNOWFLAKE_USER"),
        warehouse=os.getenv("SNOWFLAKE_WAREHOUSE"),
        database=os.getenv("SNOWFLAKE_DATABASE"),
        schema=os.getenv("SNOWFLAKE_SCHEMA"),
        authenticator=os.getenv("SNOWFLAKE_AUTHENTICATOR", "externalbrowser"),
    )

def main():
    with connect() as connection:
        cursor = connection.cursor()
        cursor.execute("select current_version()")
        print("Connected to Snowflake with environment-based configuration.")

if __name__ == "__main__":
    main()
'''


def _streamlit_dashboard(ctx: dict[str, str]) -> str:
    return f'''
import pandas as pd
import streamlit as st

CATEGORY_COLUMN = "{ctx["category"]}"
NUMERIC_COLUMN = "{ctx["numeric"]}"

def load_data():
    return pd.read_parquet("outputs/category_summary")

def main():
    st.title("Assignment 2 Snowflake/Streamlit Dashboard")
    df = load_data()
    selected = st.multiselect("Filter category", sorted(df[CATEGORY_COLUMN].dropna().unique()))
    view = df[df[CATEGORY_COLUMN].isin(selected)] if selected else df
    st.metric("Rows", len(view))
    st.bar_chart(view.set_index(CATEGORY_COLUMN)["avg_value"])
    st.dataframe(view)

if __name__ == "__main__":
    main()
'''


def _replay_producer(ctx: dict[str, str]) -> str:
    return _producer(ctx).replace("assignment-events", "streaming-events")


def _structured_streaming(ctx: dict[str, str]) -> str:
    return f'''
import json
import os
import redis
from pyspark.sql import SparkSession
from pyspark.sql.functions import avg, col, from_json, window
from pyspark.sql.types import DoubleType, StringType, StructField, StructType

SCHEMA = StructType([
    StructField("timestamp", StringType()),
    StructField("category", StringType()),
    StructField("value", DoubleType()),
])

def write_to_redis(batch_df, batch_id):
    client = redis.Redis(host=os.getenv("REDIS_HOST", "localhost"), port=int(os.getenv("REDIS_PORT", "6379")), decode_responses=True)
    for row in batch_df.collect():
        client.hset("latest_metrics", mapping={{str(row["category"]): str(row["avg_value"])}})

def main():
    spark = SparkSession.builder.appName("assignment-3-streaming").getOrCreate()
    raw = spark.readStream.format("kafka").option("kafka.bootstrap.servers", os.getenv("KAFKA_BOOTSTRAP", "localhost:9092")).option("subscribe", "streaming-events").load()
    parsed = raw.select(from_json(col("value").cast("string"), SCHEMA).alias("event")).select("event.*")
    windowed = parsed.withWatermark("timestamp", "10 minutes").groupBy(window(col("timestamp"), "5 minutes"), col("category")).agg(avg("value").alias("avg_value"))
    query = windowed.writeStream.outputMode("update").foreachBatch(write_to_redis).start()
    query.awaitTermination()

if __name__ == "__main__":
    main()
'''


def _redis_helper() -> str:
    return '''
import os
import redis

def client():
    return redis.Redis(host=os.getenv("REDIS_HOST", "localhost"), port=int(os.getenv("REDIS_PORT", "6379")), decode_responses=True)

def get_latest_metrics():
    return client().hgetall("latest_metrics")

def main():
    print(get_latest_metrics())

if __name__ == "__main__":
    main()
'''


def _live_dashboard() -> str:
    return '''
import time
import pandas as pd
import streamlit as st
from redis_helper import get_latest_metrics

def main():
    st.title("Assignment 3 Live Streaming Dashboard")
    refresh_seconds = st.slider("Refresh seconds", 1, 30, 5)
    placeholder = st.empty()
    while True:
        metrics = get_latest_metrics()
        df = pd.DataFrame([{"category": key, "value": float(value)} for key, value in metrics.items()])
        with placeholder.container():
            st.metric("Live categories", len(df))
            if not df.empty:
                st.line_chart(df.set_index("category")["value"])
                st.dataframe(df)
        time.sleep(refresh_seconds)

if __name__ == "__main__":
    main()
'''
