from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from scripts.audit_intent_dataset import (
    audit_dataset,
    detect_suspicious_examples,
    validate_dataset,
)
from scripts.merge_intent_datasets import merge_datasets


def _write_csv(path: Path, rows: list[dict[str, str]], columns=None) -> None:
    frame = pd.DataFrame(rows, columns=columns)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)


def _row(message: str, label: str, source: str = "huggingface_stackoverflow") -> dict[str, str]:
    return {
        "user_message": message,
        "final_label": label,
        "label_status": "confirmed",
        "source": source,
    }


def test_audit_validates_required_schema(tmp_path: Path):
    path = tmp_path / "bad_schema.csv"
    _write_csv(path, [{"user_message": "How to build a FastAPI endpoint?"}], columns=["user_message"])

    summary = audit_dataset(path)

    assert "Missing required columns" in summary["errors"][0]


def test_audit_rejects_invalid_labels():
    frame = pd.DataFrame([_row("How to build a FastAPI endpoint?", "invalid")])

    errors = validate_dataset(frame)

    assert errors == ["Invalid labels: invalid"]


def test_audit_rejects_invalid_statuses():
    row = _row("How to build a FastAPI endpoint?", "backend")
    row["label_status"] = "suggested"
    frame = pd.DataFrame([row])

    errors = validate_dataset(frame)

    assert errors == ["Invalid label_status values: suggested"]


def test_merge_deduplicates_with_priority_astra_over_hf_over_stacklite(tmp_path: Path):
    astra = tmp_path / "intent_examples.csv"
    hf = tmp_path / "hf.csv"
    stacklite = tmp_path / "stacklite.csv"
    duplicate = "How to build a FastAPI endpoint?"
    _write_csv(astra, [_row(duplicate, "backend", "manual")])
    _write_csv(hf, [_row(duplicate.lower(), "frontend", "huggingface_stackoverflow")])
    _write_csv(stacklite, [_row(duplicate, "runtime", "stackoverflow")])

    merged = merge_datasets((astra, hf, stacklite), tmp_path / "combined.csv", max_per_label=150)

    assert len(merged) == 1
    assert merged.iloc[0]["source"] == "manual"
    assert merged.iloc[0]["final_label"] == "backend"


def test_merge_prefers_huggingface_over_stacklite_when_no_astra(tmp_path: Path):
    hf = tmp_path / "hf.csv"
    stacklite = tmp_path / "stacklite.csv"
    duplicate = "How to debug CUDA memory?"
    _write_csv(hf, [_row(duplicate, "runtime", "huggingface_stackoverflow")])
    _write_csv(stacklite, [_row(duplicate, "debugging", "stackoverflow")])

    merged = merge_datasets((tmp_path / "missing.csv", hf, stacklite), tmp_path / "combined.csv")

    assert len(merged) == 1
    assert merged.iloc[0]["source"] == "huggingface_stackoverflow"
    assert merged.iloc[0]["final_label"] == "runtime"


def test_merge_keeps_max_examples_per_label(tmp_path: Path):
    source = tmp_path / "hf.csv"
    rows = [_row(f"How to build FastAPI endpoint {index}?", "backend") for index in range(6)]
    _write_csv(source, rows)

    merged = merge_datasets((source,), tmp_path / "combined.csv", max_per_label=3)

    assert len(merged) == 3
    assert merged["final_label"].value_counts().to_dict() == {"backend": 3}


def test_audit_detects_suspicious_examples():
    frame = pd.DataFrame(
        [
            _row("short", "backend"),
            _row("What is this????????????????????????????", "general"),
            _row("How to style a React component?", "backend"),
            _row("x" * 221, "frontend"),
        ]
    )

    suspicious = detect_suspicious_examples(frame)

    backend_reasons = " ".join(item["reasons"] for item in suspicious["backend"])
    frontend_reasons = " ".join(item["reasons"] for item in suspicious["frontend"])
    general_reasons = " ".join(item["reasons"] for item in suspicious["general"])
    assert "shorter_than_12" in backend_reasons
    assert "label_keywords_do_not_match" in backend_reasons
    assert "longer_than_220" in frontend_reasons
    assert "too_much_punctuation" in general_reasons


def test_merge_raises_on_invalid_input_dataset(tmp_path: Path):
    source = tmp_path / "bad.csv"
    _write_csv(source, [_row("How to build a FastAPI endpoint?", "oops")])

    with pytest.raises(ValueError, match="Invalid labels"):
        merge_datasets((source,), tmp_path / "combined.csv")
