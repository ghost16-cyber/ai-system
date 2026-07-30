from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any


def calculate_accuracy(results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results)
    correct = sum(1 for result in results if result.get("correct") is True)
    incorrect = total - correct
    return {
        "total": total,
        "correct": correct,
        "incorrect": incorrect,
        "accuracy": correct / total if total else 0.0,
    }


def calculate_label_counts(results: list[dict[str, Any]]) -> dict[str, Any]:
    expected = Counter(str(result.get("expected_label", "")) for result in results)
    predicted = Counter(str(result.get("predicted_label", "")) for result in results)
    return {
        "expected": dict(sorted(expected.items())),
        "predicted": dict(sorted(predicted.items())),
    }


def calculate_confusion_matrix(results: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    matrix: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for result in results:
        expected = str(result.get("expected_label", ""))
        predicted = str(result.get("predicted_label", ""))
        matrix[expected][predicted] += 1
    return {
        expected: dict(sorted(predictions.items()))
        for expected, predictions in sorted(matrix.items())
    }


def summarize_failures(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    for result in results:
        if result.get("correct") is True:
            continue
        failures.append(
            {
                "specialist": result.get("specialist"),
                "text": result.get("text"),
                "expected_label": result.get("expected_label"),
                "predicted_label": result.get("predicted_label"),
                "confidence": result.get("confidence"),
                "reason": result.get("reason"),
                "error": result.get("error"),
            }
        )
    return failures
