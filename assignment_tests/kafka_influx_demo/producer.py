import csv
import json
import os
import time
from kafka import KafkaProducer

TOPIC = os.getenv("KAFKA_TOPIC", "assignment-events")
DATASET_PATH = os.getenv("DATASET_PATH", "DATASET_PATH")

def build_event(row):
    return {
        "timestamp": row.get("TIMESTAMP_COLUMN"),
        "category": row.get("CATEGORY_COLUMN"),
        "value": float(row.get("NUMERIC_COLUMN", 0) or 0),
        "raw": row,
    }

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
