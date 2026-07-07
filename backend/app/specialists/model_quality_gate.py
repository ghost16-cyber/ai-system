from __future__ import annotations

from collections import Counter
from typing import Any


DEFAULT_QUALITY_THRESHOLDS = {
    "min_examples": 8,
    "min_labels": 2,
    "min_accuracy": 0.6,
}

DEFAULT_PROMOTION_QUALITY_THRESHOLDS = {
    "minimum_accuracy": 0.70,
}

REQUIRED_PROMOTION_METRICS = ("accuracy", "label_counts", "quality_gate")


def evaluate_quality_gate(
    *,
    specialist: str,
    examples: list[dict[str, Any]],
    accuracy: float,
    thresholds: dict[str, float | int] | None = None,
) -> dict[str, Any]:
    active_thresholds = {**DEFAULT_QUALITY_THRESHOLDS, **(thresholds or {})}
    labels = [str(example.get("expected_label", "")) for example in examples]
    label_counts = dict(sorted(Counter(labels).items()))
    failures: list[str] = []

    if len(examples) < int(active_thresholds["min_examples"]):
        failures.append(
            f"minimum examples not met: {len(examples)} < {active_thresholds['min_examples']}"
        )
    if len(label_counts) < int(active_thresholds["min_labels"]):
        failures.append(
            f"minimum labels not met: {len(label_counts)} < {active_thresholds['min_labels']}"
        )
    if len(label_counts) <= 1:
        failures.append("single-label model promotion is not allowed")
    if accuracy < float(active_thresholds["min_accuracy"]):
        failures.append(
            f"minimum accuracy not met: {accuracy:.3f} < {active_thresholds['min_accuracy']}"
        )

    return {
        "specialist": specialist,
        "passed": not failures,
        "failures": failures,
        "thresholds": active_thresholds,
        "example_count": len(examples),
        "label_counts": label_counts,
        "accuracy": accuracy,
    }


def evaluate_promotion_quality_gate(
    metadata: dict[str, Any],
    thresholds: dict[str, float | int] | None = None,
) -> dict[str, Any]:
    active_thresholds = {**DEFAULT_PROMOTION_QUALITY_THRESHOLDS, **(thresholds or {})}
    failures: list[str] = []

    lifecycle_status = metadata.get("lifecycle_status")
    if lifecycle_status != "candidate":
        failures.append(
            f"Only candidate models can be promoted; found {lifecycle_status or 'unknown'}."
        )

    metrics = metadata.get("metrics")
    if not isinstance(metrics, dict):
        failures.append("Promotion blocked: model has no stored metrics.")
        metrics = {}

    for metric_name in REQUIRED_PROMOTION_METRICS:
        if metric_name not in metrics:
            failures.append(f"Promotion blocked: required metric missing: {metric_name}.")

    accuracy = metrics.get("accuracy")
    if isinstance(accuracy, (int, float)):
        minimum_accuracy = float(active_thresholds["minimum_accuracy"])
        if float(accuracy) < minimum_accuracy:
            failures.append(
                f"Promotion blocked: accuracy {float(accuracy):.3f} is below minimum {minimum_accuracy:.3f}."
            )
    elif "accuracy" in metrics:
        failures.append("Promotion blocked: accuracy metric must be numeric.")

    quality_gate = metrics.get("quality_gate")
    if isinstance(quality_gate, dict) and quality_gate.get("passed") is not True:
        failures.append("Promotion blocked: training quality gate did not pass.")

    return {
        "passed": not failures,
        "failures": failures,
        "thresholds": active_thresholds,
        "required_metrics": list(REQUIRED_PROMOTION_METRICS),
        "metrics": metrics,
    }
