import csv
import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "backend" / "data" / "training"

REQUIRED_FILES = [
    DATA_DIR / "intent_examples.csv",
    DATA_DIR / "error_examples.csv",
    DATA_DIR / "label_schema.json",
]

def read_csv(path):
    with open(path, "r", encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))

def main():
    print("Astra dataset validation")
    print("=" * 60)

    missing = [path for path in REQUIRED_FILES if not path.exists()]
    if missing:
        print("Missing files:")
        for path in missing:
            print(f"- {path}")
        raise SystemExit(1)

    intent_rows = read_csv(DATA_DIR / "intent_examples.csv")
    error_rows = read_csv(DATA_DIR / "error_examples.csv")

    with open(DATA_DIR / "label_schema.json", "r", encoding="utf-8") as file:
        schema = json.load(file)

    print(f"Intent examples: {len(intent_rows)}")
    print(f"Intent labels:   {len(set(row['intent'] for row in intent_rows))}")
    print(f"Error examples:  {len(error_rows)}")
    print(f"Error labels:    {len(set(row['error_type'] for row in error_rows))}")

    required_intent_cols = {"text", "intent", "domain", "risk_hint", "notes"}
    required_error_cols = {"error_text", "error_type", "ecosystem", "severity", "recommended_skill", "notes"}

    if not intent_rows or set(intent_rows[0].keys()) != required_intent_cols:
        raise SystemExit("Intent dataset columns are invalid.")

    if not error_rows or set(error_rows[0].keys()) != required_error_cols:
        raise SystemExit("Error dataset columns are invalid.")

    empty_intents = [row for row in intent_rows if not row["text"].strip() or not row["intent"].strip()]
    empty_errors = [row for row in error_rows if not row["error_text"].strip() or not row["error_type"].strip()]

    if empty_intents:
        raise SystemExit(f"Found {len(empty_intents)} empty intent rows.")

    if empty_errors:
        raise SystemExit(f"Found {len(empty_errors)} empty error rows.")

    print("Schema purpose:", schema.get("purpose", "N/A"))
    print("Validation passed.")

if __name__ == "__main__":
    main()
