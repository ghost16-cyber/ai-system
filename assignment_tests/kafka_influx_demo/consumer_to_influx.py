import json
import os
from kafka import KafkaConsumer
from influxdb_client import InfluxDBClient, Point

TOPIC = os.getenv("KAFKA_TOPIC", "assignment-events")

def point_from_event(event):
    return (
        Point("assignment_metric")
        .tag("category", str(event.get("category", "unknown")))
        .field("NUMERIC_COLUMN", float(event.get("value", 0) or 0))
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
