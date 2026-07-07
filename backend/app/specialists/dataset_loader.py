from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DEFAULT_SPECIALIST_EVAL_DATASET = Path("data/specialists/specialist_eval_dataset.jsonl")


def validate_dataset_row(row: Any) -> tuple[dict[str, Any] | None, list[str]]:
    errors: list[str] = []
    if not isinstance(row, dict):
        return None, ["row must be a JSON object"]

    specialist = row.get("specialist")
    text = row.get("text")
    expected_label = row.get("expected_label")

    if not isinstance(specialist, str) or not specialist.strip():
        errors.append("specialist must be a non-empty string")
    if not isinstance(text, str) or not text.strip():
        errors.append("text must be a non-empty string")
    if not isinstance(expected_label, str) or not expected_label.strip():
        errors.append("expected_label must be a non-empty string")

    metadata = row.get("metadata", {})
    if metadata is None:
        metadata = {}
    if not isinstance(metadata, dict):
        errors.append("metadata must be an object when provided")

    if errors:
        return None, errors

    return {
        "specialist": specialist.strip(),
        "text": text,
        "expected_label": expected_label.strip(),
        "metadata": metadata,
    }, []


def load_jsonl_dataset(path: str | Path) -> dict[str, Any]:
    dataset_path = Path(path)
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    if not dataset_path.exists():
        return {
            "path": str(dataset_path),
            "rows": rows,
            "errors": errors,
            "missing": True,
        }

    with dataset_path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                raw_row = json.loads(stripped)
            except json.JSONDecodeError as error:
                errors.append(
                    {
                        "line": line_number,
                        "errors": [f"invalid json: {error.msg}"],
                    }
                )
                continue

            row, row_errors = validate_dataset_row(raw_row)
            if row is None:
                errors.append({"line": line_number, "errors": row_errors})
                continue
            rows.append(row)

    return {
        "path": str(dataset_path),
        "rows": rows,
        "errors": errors,
        "missing": False,
    }


def load_specialist_eval_dataset(path: str | Path | None = None) -> dict[str, Any]:
    return load_jsonl_dataset(path or DEFAULT_SPECIALIST_EVAL_DATASET)
