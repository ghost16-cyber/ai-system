from __future__ import annotations

from pathlib import Path
from typing import Any

from .model_store import find_specialist_model


def build_model_evaluation_report(
    model_id: str,
    model_dir: str | Path | None = None,
) -> dict[str, Any] | None:
    located = find_specialist_model(model_id, model_dir)
    if located is None:
        return None

    metadata = located["artifact"]["metadata"]
    metrics = metadata.get("metrics") if isinstance(metadata.get("metrics"), dict) else {}
    label_distribution = metrics.get("label_counts") or metadata.get("label_counts")
    sample_count = None
    if isinstance(metadata.get("train_examples"), int) and isinstance(metadata.get("test_examples"), int):
        sample_count = metadata["train_examples"] + metadata["test_examples"]

    return {
        "model_id": metadata.get("model_id"),
        "specialist_name": metadata.get("specialist"),
        "model_status": metadata.get("lifecycle_status"),
        "current_lifecycle_status": metadata.get("lifecycle_status"),
        "dataset_id": metadata.get("dataset_id"),
        "training_job_id": metadata.get("training_job_id"),
        "metrics": metrics,
        "created_at": metadata.get("created_at"),
        "promoted_at": metadata.get("promoted_at"),
        "sample_count": sample_count,
        "label_distribution": label_distribution,
        "confusion_matrix": metrics.get("confusion_matrix"),
        "precision": metrics.get("precision"),
        "recall": metrics.get("recall"),
        "f1_score": metrics.get("f1_score"),
        "path": located["path"],
        "advisory_only": metadata.get("advisory_only") is True,
    }
