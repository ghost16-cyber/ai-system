#!/usr/bin/env python3
"""
Train a candidate Astra intent classifier from the curated intent dataset.

This script is intentionally safe:
- It trains locally only.
- It saves a candidate model artifact only.
- It does NOT auto-promote the model.
- It does NOT change runtime routing behavior.
- It does NOT call any backend endpoint.

Expected dataset:
    data/specialists/intent_examples_curated.csv

Expected columns:
    text,label

The script also supports common alternatives like:
    prompt,intent
    query,task_type
    example,category
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    classification_report,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
)
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline


PROJECT_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_DATASET_PATH = PROJECT_ROOT / "data" / "specialists" / "intent_examples_curated.csv"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "specialists" / "models" / "candidates"


TEXT_COLUMN_CANDIDATES = [
    "text",
    "user_message",
    "prompt",
    "query",
    "input",
    "example",
    "utterance",
    "message",
]

LABEL_COLUMN_CANDIDATES = [
    "label",
    "final_label",
    "intent",
    "task_type",
    "category",
    "specialist",
]


@dataclass(frozen=True)
class CandidateMetadata:
    model_id: str
    model_type: str
    status: str
    created_at: str
    dataset_path: str
    total_examples: int
    label_distribution: dict[str, int]
    labels: list[str]
    train_examples: int
    test_examples: int
    test_size: float
    random_state: int
    accuracy: float
    macro_precision: float
    macro_recall: float
    macro_f1: float
    weighted_precision: float
    weighted_recall: float
    weighted_f1: float
    artifact_path: str
    metadata_path: str
    notes: list[str]


def resolve_column(fieldnames: list[str], candidates: list[str], column_type: str) -> str:
    normalized = {name.lower().strip(): name for name in fieldnames}

    for candidate in candidates:
        if candidate in normalized:
            return normalized[candidate]

    raise ValueError(
        f"Could not find a {column_type} column. "
        f"Expected one of {candidates}, found {fieldnames}."
    )


def load_dataset(dataset_path: Path) -> tuple[list[str], list[str], str, str]:
    if not dataset_path.exists():
        raise FileNotFoundError(f"Dataset not found: {dataset_path}")

    texts: list[str] = []
    labels: list[str] = []

    with dataset_path.open("r", encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)

        if not reader.fieldnames:
            raise ValueError("Dataset CSV has no header row.")

        text_column = resolve_column(reader.fieldnames, TEXT_COLUMN_CANDIDATES, "text")
        label_column = resolve_column(reader.fieldnames, LABEL_COLUMN_CANDIDATES, "label")

        for row_number, row in enumerate(reader, start=2):
            text = (row.get(text_column) or "").strip()
            label = (row.get(label_column) or "").strip()

            if not text:
                raise ValueError(f"Empty text value on CSV row {row_number}.")

            if not label:
                raise ValueError(f"Empty label value on CSV row {row_number}.")

            texts.append(text)
            labels.append(label)

    if not texts:
        raise ValueError("Dataset is empty.")

    return texts, labels, text_column, label_column


def validate_dataset(texts: list[str], labels: list[str], min_examples_per_label: int) -> dict[str, int]:
    if len(texts) != len(labels):
        raise ValueError("Texts and labels have different lengths.")

    distribution = Counter(labels)

    if len(distribution) < 2:
        raise ValueError("Training requires at least 2 intent classes.")

    too_small = {
        label: count
        for label, count in distribution.items()
        if count < min_examples_per_label
    }

    if too_small:
        raise ValueError(
            "Some labels do not have enough examples for safe training: "
            f"{too_small}. Minimum required per label: {min_examples_per_label}."
        )

    return dict(sorted(distribution.items()))


def build_pipeline() -> Pipeline:
    return Pipeline(
        steps=[
            (
                "tfidf",
                TfidfVectorizer(
                    lowercase=True,
                    strip_accents="unicode",
                    ngram_range=(1, 2),
                    min_df=1,
                    max_df=0.95,
                    sublinear_tf=True,
                ),
            ),
            (
                "classifier",
                LogisticRegression(
                    max_iter=2000,
                    class_weight="balanced",
                    solver="lbfgs",
                    random_state=42,
                ),
            ),
        ]
    )


def train_candidate(
    dataset_path: Path,
    output_dir: Path,
    test_size: float,
    random_state: int,
    min_examples_per_label: int,
) -> CandidateMetadata:
    texts, labels, text_column, label_column = load_dataset(dataset_path)
    label_distribution = validate_dataset(texts, labels, min_examples_per_label)

    x_train, x_test, y_train, y_test = train_test_split(
        texts,
        labels,
        test_size=test_size,
        random_state=random_state,
        stratify=labels,
    )

    pipeline = build_pipeline()
    pipeline.fit(x_train, y_train)

    predictions = pipeline.predict(x_test)

    labels_sorted = sorted(label_distribution.keys())

    accuracy = accuracy_score(y_test, predictions)
    macro_precision = precision_score(y_test, predictions, average="macro", zero_division=0)
    macro_recall = recall_score(y_test, predictions, average="macro", zero_division=0)
    macro_f1 = f1_score(y_test, predictions, average="macro", zero_division=0)
    weighted_precision = precision_score(y_test, predictions, average="weighted", zero_division=0)
    weighted_recall = recall_score(y_test, predictions, average="weighted", zero_division=0)
    weighted_f1 = f1_score(y_test, predictions, average="weighted", zero_division=0)

    created_at = datetime.now(timezone.utc).isoformat()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    model_id = f"intent_classifier_curated_{timestamp}"

    candidate_dir = output_dir / model_id
    candidate_dir.mkdir(parents=True, exist_ok=False)

    artifact_path = candidate_dir / "model.joblib"
    metadata_path = candidate_dir / "metadata.json"
    report_path = candidate_dir / "evaluation_report.json"

    joblib.dump(pipeline, artifact_path)

    report: dict[str, Any] = {
        "model_id": model_id,
        "status": "candidate",
        "dataset_path": str(dataset_path),
        "text_column": text_column,
        "label_column": label_column,
        "labels": labels_sorted,
        "label_distribution": label_distribution,
        "metrics": {
            "accuracy": accuracy,
            "macro_precision": macro_precision,
            "macro_recall": macro_recall,
            "macro_f1": macro_f1,
            "weighted_precision": weighted_precision,
            "weighted_recall": weighted_recall,
            "weighted_f1": weighted_f1,
        },
        "confusion_matrix": {
            "labels": labels_sorted,
            "matrix": confusion_matrix(y_test, predictions, labels=labels_sorted).tolist(),
        },
        "classification_report": classification_report(
            y_test,
            predictions,
            labels=labels_sorted,
            zero_division=0,
            output_dict=True,
        ),
    }

    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    metadata = CandidateMetadata(
        model_id=model_id,
        model_type="sklearn_tfidf_logistic_regression_intent_classifier",
        status="candidate",
        created_at=created_at,
        dataset_path=str(dataset_path),
        total_examples=len(texts),
        label_distribution=label_distribution,
        labels=labels_sorted,
        train_examples=len(x_train),
        test_examples=len(x_test),
        test_size=test_size,
        random_state=random_state,
        accuracy=round(float(accuracy), 6),
        macro_precision=round(float(macro_precision), 6),
        macro_recall=round(float(macro_recall), 6),
        macro_f1=round(float(macro_f1), 6),
        weighted_precision=round(float(weighted_precision), 6),
        weighted_recall=round(float(weighted_recall), 6),
        weighted_f1=round(float(weighted_f1), 6),
        artifact_path=str(artifact_path),
        metadata_path=str(metadata_path),
        notes=[
            "Candidate model only.",
            "No automatic promotion was performed.",
            "No runtime routing behavior was changed.",
            "Manual evaluation and promotion should happen in a separate phase.",
        ],
    )

    metadata_path.write_text(json.dumps(asdict(metadata), indent=2), encoding="utf-8")

    return metadata


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Train a candidate Astra intent classifier from the curated dataset."
    )

    parser.add_argument(
        "--dataset",
        type=Path,
        default=DEFAULT_DATASET_PATH,
        help=f"Path to curated CSV dataset. Default: {DEFAULT_DATASET_PATH}",
    )

    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Directory where candidate model artifacts are saved. Default: {DEFAULT_OUTPUT_DIR}",
    )

    parser.add_argument(
        "--test-size",
        type=float,
        default=0.2,
        help="Validation/test split size. Default: 0.2",
    )

    parser.add_argument(
        "--random-state",
        type=int,
        default=42,
        help="Random state for reproducible training. Default: 42",
    )

    parser.add_argument(
        "--min-examples-per-label",
        type=int,
        default=5,
        help="Minimum examples required per label. Default: 5",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    metadata = train_candidate(
        dataset_path=args.dataset,
        output_dir=args.output_dir,
        test_size=args.test_size,
        random_state=args.random_state,
        min_examples_per_label=args.min_examples_per_label,
    )

    print()
    print("Candidate intent classifier trained successfully.")
    print("No promotion was performed.")
    print("Runtime behavior was not changed.")
    print()
    print(f"Model ID:      {metadata.model_id}")
    print(f"Status:        {metadata.status}")
    print(f"Examples:      {metadata.total_examples}")
    print(f"Train/Test:    {metadata.train_examples}/{metadata.test_examples}")
    print(f"Accuracy:      {metadata.accuracy}")
    print(f"Macro F1:      {metadata.macro_f1}")
    print(f"Weighted F1:   {metadata.weighted_f1}")
    print()
    print(f"Artifact:      {metadata.artifact_path}")
    print(f"Metadata:      {metadata.metadata_path}")
    print()


if __name__ == "__main__":
    main()