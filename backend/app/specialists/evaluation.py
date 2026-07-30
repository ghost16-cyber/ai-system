from __future__ import annotations

from typing import Any

from .dataset_loader import load_specialist_eval_dataset
from .metrics import (
    calculate_accuracy,
    calculate_confusion_matrix,
    calculate_label_counts,
    summarize_failures,
)
from .registry import predict_with_specialist
from .schemas import SpecialistRequest


def _empty_specialist_summary() -> dict[str, Any]:
    return {
        "total_examples": 0,
        "correct": 0,
        "incorrect": 0,
        "accuracy": 0.0,
    }


def _summarize_by_specialist(results: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_specialist: dict[str, dict[str, Any]] = {}
    for result in results:
        specialist = str(result.get("specialist", "unknown"))
        summary = by_specialist.setdefault(specialist, _empty_specialist_summary())
        summary["total_examples"] += 1
        if result.get("correct") is True:
            summary["correct"] += 1
        else:
            summary["incorrect"] += 1

    for summary in by_specialist.values():
        total = summary["total_examples"]
        summary["accuracy"] = summary["correct"] / total if total else 0.0
    return dict(sorted(by_specialist.items()))


def evaluate_examples(examples: list[dict[str, Any]]) -> dict[str, Any]:
    results: list[dict[str, Any]] = []

    for example in examples:
        specialist = str(example.get("specialist", ""))
        text = str(example.get("text", ""))
        expected_label = str(example.get("expected_label", ""))
        try:
            prediction = predict_with_specialist(
                specialist,
                SpecialistRequest(
                    text=text,
                    context={"metadata": example.get("metadata", {})},
                ),
            )
            if prediction.advisory_only is not True:
                raise ValueError("specialist prediction was not advisory_only")

            predicted_label = prediction.label
            results.append(
                {
                    "specialist": prediction.specialist,
                    "text": text,
                    "expected_label": expected_label,
                    "predicted_label": predicted_label,
                    "correct": predicted_label == expected_label,
                    "confidence": prediction.confidence,
                    "features": prediction.features,
                    "reason": prediction.reason,
                    "model_version": prediction.model_version,
                    "advisory_only": prediction.advisory_only,
                    "metadata": example.get("metadata", {}),
                }
            )
        except Exception as error:
            results.append(
                {
                    "specialist": specialist,
                    "text": text,
                    "expected_label": expected_label,
                    "predicted_label": None,
                    "correct": False,
                    "confidence": 0.0,
                    "features": {},
                    "reason": "Evaluation could not obtain a specialist prediction.",
                    "model_version": "rules_v1",
                    "advisory_only": True,
                    "metadata": example.get("metadata", {}),
                    "error": str(error),
                }
            )

    accuracy = calculate_accuracy(results)
    failures = summarize_failures(results)
    return {
        "total_examples": accuracy["total"],
        "correct": accuracy["correct"],
        "incorrect": accuracy["incorrect"],
        "accuracy": accuracy["accuracy"],
        "by_specialist": _summarize_by_specialist(results),
        "label_counts": calculate_label_counts(results),
        "confusion_matrix": calculate_confusion_matrix(results),
        "failures": failures,
        "results": results,
    }


def evaluate_specialist_dataset(dataset_path: str | None = None) -> dict[str, Any]:
    loaded = load_specialist_eval_dataset(dataset_path)
    summary = evaluate_examples(loaded["rows"])
    summary["dataset"] = {
        "path": loaded["path"],
        "missing": loaded["missing"],
        "loaded_examples": len(loaded["rows"]),
        "validation_errors": loaded["errors"],
    }
    return summary
