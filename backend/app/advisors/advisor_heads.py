from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from .repair_labels import RepairAdvisorInput

HEAD_FIELDS: tuple[str, ...] = ("bug_type", "source_file", "difficulty", "patch_risk")


@dataclass(frozen=True)
class HeadPrediction:
    value: str | None
    confidence: float


@dataclass
class ConstantHead:
    """Picklable fallback model for heads that only have one training label."""

    label: str

    def predict(self, texts: Sequence[str]) -> list[str]:
        return [self.label for _ in texts]

    def predict_proba(self, texts: Sequence[str]) -> list[list[float]]:
        return [[1.0] for _ in texts]


def prediction_tuple_to_dict(values: Sequence[str | None]) -> dict[str, str | None]:
    return dict(
        zip(
            HEAD_FIELDS,
            [None if value is None else str(value) for value in values],
        )
    )


def predict_head(model: Any, text: str) -> HeadPrediction:
    value = model.predict([text])[0]
    confidence = 0.5

    if hasattr(model, "predict_proba"):
        try:
            probabilities = model.predict_proba([text])[0]
            confidence = max(float(value) for value in probabilities)
        except Exception:
            confidence = 0.5

    return HeadPrediction(
        value=None if value is None else str(value),
        confidence=round(float(confidence), 4),
    )


def calibrated_head_prediction(
    *,
    head: str,
    prediction: HeadPrediction,
    input_data: RepairAdvisorInput,
) -> HeadPrediction:
    """Convert raw model probability into a more useful shadow advisor confidence.

    With small local benchmark sets, some heads can be under-confident even when
    the predicted label is directly present in the task context. Calibration keeps
    the advisor useful for measurement without letting it control execution.
    """

    value = prediction.value
    confidence = prediction.confidence

    if not value:
        return prediction

    if head == "source_file":
        visible_files = set(input_data.candidate_files or []) | set(input_data.inspected_files or [])
        if value in visible_files:
            confidence = max(confidence, 0.85)
    elif head == "bug_type" and value != "unknown":
        confidence = max(confidence, 0.72)
    elif head == "difficulty" and value != "unknown":
        confidence = max(confidence, 0.7)
    elif head == "patch_risk" and value != "unknown":
        confidence = max(confidence, 0.7)

    return HeadPrediction(value=value, confidence=round(confidence, 4))


def average_confidence(predictions: Sequence[HeadPrediction]) -> float:
    values = [prediction.confidence for prediction in predictions if prediction.value is not None]
    return round(sum(values) / len(values), 4) if values else 0.0


def extract_head_confidences(probas: Sequence[Sequence[float]]) -> dict[str, float]:
    return {
        head: round(float(max(probabilities)), 4)
        for head, probabilities in zip(HEAD_FIELDS, probas)
    }
