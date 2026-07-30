from __future__ import annotations

import argparse
import re
from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd


REQUIRED_COLUMNS = ("user_message", "final_label", "label_status", "source")
VALID_LABELS = (
    "backend",
    "frontend",
    "debugging",
    "testing",
    "rag",
    "training",
    "runtime",
    "general",
)
VALID_STATUSES = ("confirmed", "corrected")

LABEL_KEYWORDS: dict[str, tuple[str, ...]] = {
    "backend": (
        "fastapi",
        "django",
        "flask",
        " api ",
        " rest ",
        "sqlalchemy",
        "postgresql",
        "mysql",
        "sqlite",
        "database",
        "backend",
        "endpoint",
    ),
    "frontend": (
        "react",
        "vite",
        "typescript",
        "javascript",
        " html ",
        " css ",
        "frontend",
        "vue",
        "angular",
        "component",
    ),
    "debugging": (
        "error",
        "exception",
        "traceback",
        "bug",
        "crash",
        "failed",
        "not working",
        "debug",
    ),
    "testing": ("pytest", "unittest", "jest", "testing", "unit test", "mock", "coverage"),
    "rag": (
        "embedding",
        "vector",
        "langchain",
        "chromadb",
        "qdrant",
        "faiss",
        "retrieval",
        "semantic search",
    ),
    "training": (
        "scikit",
        "sklearn",
        "machine learning",
        "classification",
        "training",
        "dataset",
        "accuracy",
        "model",
        " fit ",
    ),
    "runtime": (
        "cuda",
        "gpu",
        "pytorch",
        "ollama",
        "node.js",
        "npm",
        "uvicorn",
        "memory",
        "performance",
        "server",
        "deployment",
    ),
    "general": (
        "how to",
        "what is",
        "why",
        "best way",
        "explain",
        "difference",
        "approach",
        "design",
    ),
}


def audit_dataset(path: str | Path, *, random_examples: int = 10) -> dict[str, Any]:
    frame = read_dataset(path)
    errors = validate_dataset(frame)
    has_required_columns = all(column in frame.columns for column in REQUIRED_COLUMNS)
    duplicate_count = (
        int(frame["user_message"].map(normalize_message).duplicated().sum())
        if "user_message" in frame.columns
        else 0
    )
    suspicious = detect_suspicious_examples(frame)
    summary = {
        "path": str(path),
        "total_rows": len(frame),
        "errors": errors,
        "label_distribution": (
            frame["final_label"].value_counts().sort_index().to_dict()
            if "final_label" in frame.columns
            else {}
        ),
        "source_distribution": (
            frame["source"].value_counts().sort_index().to_dict()
            if "source" in frame.columns
            else {}
        ),
        "duplicate_count": duplicate_count,
        "shortest_messages": _message_extremes(frame, shortest=True) if "user_message" in frame.columns else [],
        "longest_messages": _message_extremes(frame, shortest=False) if "user_message" in frame.columns else [],
        "random_examples": random_examples_by_label(frame, limit=random_examples) if has_required_columns else {},
        "suspicious_examples": suspicious,
    }
    return summary


def read_dataset(path: str | Path) -> pd.DataFrame:
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def validate_dataset(frame: pd.DataFrame) -> list[str]:
    errors: list[str] = []
    missing = [column for column in REQUIRED_COLUMNS if column not in frame.columns]
    if missing:
        errors.append(f"Missing required columns: {', '.join(missing)}")
        return errors
    invalid_labels = sorted(set(frame["final_label"]) - set(VALID_LABELS))
    if invalid_labels:
        errors.append(f"Invalid labels: {', '.join(invalid_labels)}")
    invalid_statuses = sorted(set(frame["label_status"]) - set(VALID_STATUSES))
    if invalid_statuses:
        errors.append(f"Invalid label_status values: {', '.join(invalid_statuses)}")
    return errors


def detect_suspicious_examples(frame: pd.DataFrame, *, limit_per_label: int = 20) -> dict[str, list[dict[str, str]]]:
    suspicious: dict[str, list[dict[str, str]]] = {label: [] for label in VALID_LABELS}
    if any(column not in frame.columns for column in REQUIRED_COLUMNS):
        return suspicious
    for _, row in frame.iterrows():
        label = str(row["final_label"])
        if label not in suspicious or len(suspicious[label]) >= limit_per_label:
            continue
        message = str(row["user_message"])
        reasons = suspicious_reasons(message, label)
        if reasons:
            suspicious[label].append(
                {
                    "user_message": message,
                    "final_label": label,
                    "source": str(row["source"]),
                    "reasons": "; ".join(reasons),
                }
            )
    return suspicious


def suspicious_reasons(message: str, label: str) -> list[str]:
    reasons: list[str] = []
    text = message.strip()
    if len(text) < 12:
        reasons.append("shorter_than_12")
    if len(text) > 220:
        reasons.append("longer_than_220")
    if too_much_punctuation(text):
        reasons.append("too_much_punctuation")
    if not label_keywords_match(text, label):
        reasons.append("label_keywords_do_not_match")
    return reasons


def label_keywords_match(message: str, label: str) -> bool:
    keywords = LABEL_KEYWORDS.get(label, ())
    haystack = f" {message.lower()} "
    return any(keyword in haystack for keyword in keywords)


def too_much_punctuation(message: str) -> bool:
    if len(message) < 20:
        return False
    punctuation = sum(1 for character in message if not character.isalnum() and not character.isspace())
    return punctuation / max(1, len(message)) > 0.3


def random_examples_by_label(frame: pd.DataFrame, *, limit: int) -> dict[str, list[str]]:
    examples: dict[str, list[str]] = {}
    for label in VALID_LABELS:
        subset = frame[frame["final_label"] == label]
        if subset.empty:
            examples[label] = []
            continue
        examples[label] = subset.sample(n=min(limit, len(subset)), random_state=52)["user_message"].tolist()
    return examples


def normalize_message(message: str) -> str:
    return re.sub(r"\s+", " ", str(message).strip().lower())


def _message_extremes(frame: pd.DataFrame, *, shortest: bool, limit: int = 10) -> list[str]:
    ordered = frame.assign(_length=frame["user_message"].map(lambda value: len(str(value))))
    ordered = ordered.sort_values("_length", ascending=shortest)
    return ordered["user_message"].head(limit).tolist()


def print_audit(summary: dict[str, Any]) -> None:
    print(f"Path: {summary['path']}")
    print(f"Total rows: {summary['total_rows']}")
    print("Validation:")
    if summary["errors"]:
        for error in summary["errors"]:
            print(f"- {error}")
    else:
        print("- ok")
    _print_distribution("Label distribution", summary["label_distribution"])
    _print_distribution("Source distribution", summary["source_distribution"])
    print(f"Duplicate count: {summary['duplicate_count']}")
    print("Shortest messages:")
    for item in summary["shortest_messages"]:
        print(f"- {item}")
    print("Longest messages:")
    for item in summary["longest_messages"]:
        print(f"- {item}")
    print("Random examples per label:")
    for label, examples in summary["random_examples"].items():
        print(f"{label}:")
        for item in examples:
            print(f"- {item}")
    print("Suspicious examples per label:")
    for label, examples in summary["suspicious_examples"].items():
        print(f"{label}:")
        for item in examples:
            print(f"- [{item['reasons']}] {item['user_message']}")


def _print_distribution(title: str, distribution: dict[str, int]) -> None:
    print(f"{title}:")
    for key, value in distribution.items():
        print(f"- {key}: {value}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit an Astra intent dataset CSV.")
    parser.add_argument("path", help="Path to the intent dataset CSV.")
    args = parser.parse_args()
    print_audit(audit_dataset(args.path))


if __name__ == "__main__":
    main()
