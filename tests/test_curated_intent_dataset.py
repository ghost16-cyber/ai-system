from __future__ import annotations

from pathlib import Path

import pandas as pd

from scripts.build_curated_intent_dataset import (
    REQUIRED_COLUMNS,
    VALID_LABELS,
    build_curated_dataset,
    normalize_message,
)


def test_curated_dataset_schema(tmp_path: Path):
    output = tmp_path / "intent_examples_curated.csv"
    frame = build_curated_dataset(output)

    assert output.exists()
    assert list(frame.columns) == list(REQUIRED_COLUMNS)
    saved = pd.read_csv(output)
    assert list(saved.columns) == list(REQUIRED_COLUMNS)


def test_curated_dataset_label_validity(tmp_path: Path):
    frame = build_curated_dataset(tmp_path / "curated.csv")

    assert set(frame["final_label"]) == set(VALID_LABELS)


def test_curated_dataset_minimum_examples_per_label(tmp_path: Path):
    frame = build_curated_dataset(tmp_path / "curated.csv")
    distribution = frame["final_label"].value_counts().to_dict()

    assert all(distribution[label] >= 50 for label in VALID_LABELS)


def test_curated_dataset_deduplicates_normalized_messages(tmp_path: Path):
    frame = build_curated_dataset(tmp_path / "curated.csv")
    normalized = frame["user_message"].map(normalize_message)

    assert normalized.duplicated().sum() == 0


def test_curated_dataset_source_and_status_values(tmp_path: Path):
    frame = build_curated_dataset(tmp_path / "curated.csv")

    assert set(frame["source"]) == {"curated"}
    assert set(frame["label_status"]) == {"confirmed"}


def test_curated_dataset_message_lengths(tmp_path: Path):
    frame = build_curated_dataset(tmp_path / "curated.csv")
    lengths = frame["user_message"].map(len)

    assert lengths.min() >= 15
    assert lengths.max() <= 220
