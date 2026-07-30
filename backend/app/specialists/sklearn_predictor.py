from __future__ import annotations

from typing import Any

from .model_audit_logger import append_model_audit_event
from .model_store import load_specialist_model
from .schemas import ErrorClassification, IntentPrediction, SpecialistPrediction, SpecialistRequest


def _coerce_request(payload: SpecialistRequest | dict) -> SpecialistRequest:
    if isinstance(payload, SpecialistRequest):
        return payload
    return SpecialistRequest.model_validate(payload)


def predict_with_sklearn_model(
    specialist: str,
    payload: SpecialistRequest | dict,
    model_dir: str | None = None,
) -> SpecialistPrediction | None:
    artifact = load_specialist_model(specialist, model_dir)
    if artifact is None:
        return None

    request = _coerce_request(payload)
    pipeline = artifact["pipeline"]
    metadata = artifact["metadata"]

    try:
        label = str(pipeline.predict([request.text])[0])
        confidence = _prediction_confidence(pipeline, request.text, label)
    except Exception:
        return None
    append_model_audit_event(
        action="model_used_for_prediction",
        model_id=metadata.get("model_id"),
        specialist=specialist,
        details={
            "label": label,
            "confidence": confidence,
            "model_version": metadata.get("model_version"),
        },
    )

    features: dict[str, Any] = {
        "source": "sklearn",
        "model_id": metadata.get("model_id"),
        "lifecycle_status": metadata.get("lifecycle_status"),
        "model_type": metadata.get("model_type"),
        "train_examples": metadata.get("train_examples"),
        "test_examples": metadata.get("test_examples"),
        "quality_gate": metadata.get("quality_gate"),
        "fallback_available": True,
    }
    reason = "Optional sklearn specialist model prediction; advisory only."

    try:
        if specialist == "intent_classifier":
            return IntentPrediction(
                label=label,  # type: ignore[arg-type]
                confidence=confidence,
                features=features,
                reason=reason,
                model_version=metadata.get("model_version", "sklearn_tfidf_logreg_v1"),
                advisory_only=True,
            )
        if specialist == "error_classifier":
            return ErrorClassification(
                label=label,  # type: ignore[arg-type]
                confidence=confidence,
                features=features,
                reason=reason,
                model_version=metadata.get("model_version", "sklearn_tfidf_logreg_v1"),
                advisory_only=True,
            )
        return SpecialistPrediction(
            specialist=specialist,
            label=label,
            confidence=confidence,
            features=features,
            reason=reason,
            model_version=metadata.get("model_version", "sklearn_tfidf_logreg_v1"),
            advisory_only=True,
        )
    except Exception:
        return None


def _prediction_confidence(pipeline: Any, text: str, label: str) -> float:
    if not hasattr(pipeline, "predict_proba"):
        return 0.5

    probabilities = pipeline.predict_proba([text])[0]
    classes = list(pipeline.classes_)
    if label not in classes:
        return float(max(probabilities))
    return float(probabilities[classes.index(label)])
