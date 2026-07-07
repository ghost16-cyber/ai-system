from __future__ import annotations

from collections.abc import Callable

from .error_classifier import classify_error
from .intent_classifier import predict_intent
from .model_audit_logger import append_model_audit_event
from .model_store import load_specialist_model
from .schemas import SpecialistPrediction, SpecialistRequest
from .sklearn_predictor import predict_with_sklearn_model
from .trace_store import append_specialist_trace


Predictor = Callable[[SpecialistRequest | dict], SpecialistPrediction]

_SPECIALISTS: dict[str, Predictor] = {
    "intent": predict_intent,
    "intent_classifier": predict_intent,
    "error": classify_error,
    "error_classifier": classify_error,
}


def list_specialists() -> list[str]:
    return ["intent_classifier", "error_classifier"]


def predict_with_specialist(
    name: str,
    payload: SpecialistRequest | dict,
) -> SpecialistPrediction:
    request = payload if isinstance(payload, SpecialistRequest) else SpecialistRequest.model_validate(payload)
    normalized = name.strip().lower().replace("-", "_")
    predictor = _SPECIALISTS.get(normalized)
    if predictor is None:
        raise KeyError(f"Unknown specialist: {name}")
    canonical = "intent_classifier" if normalized == "intent" else normalized
    canonical = "error_classifier" if canonical == "error" else canonical
    model_dir = request.context.get("model_dir")
    active_model = load_specialist_model(canonical, model_dir)
    if model_dir is None:
        sklearn_prediction = predict_with_sklearn_model(canonical, request)
    else:
        sklearn_prediction = predict_with_sklearn_model(canonical, request, model_dir)
    if sklearn_prediction is not None:
        _trace_prediction(
            request=request,
            prediction=sklearn_prediction,
            model_id=sklearn_prediction.features.get("model_id"),
            promoted_model_available=True,
            fallback_required=False,
            fallback_used=False,
            decision_source="sklearn_model",
        )
        return sklearn_prediction
    append_model_audit_event(
        action="fallback_used",
        specialist=canonical,
        details={"fallback": "rule_based", "reason": "No promoted sklearn model available."},
    )
    prediction = predictor(request)
    _trace_prediction(
        request=request,
        prediction=prediction,
        model_id=active_model["metadata"].get("model_id") if active_model else None,
        promoted_model_available=active_model is not None,
        fallback_required=active_model is None,
        fallback_used=True,
        decision_source="rule_fallback",
    )
    return prediction


def predict_all(payload: SpecialistRequest | dict) -> list[SpecialistPrediction]:
    return [
        predict_with_specialist("intent_classifier", payload),
        predict_with_specialist("error_classifier", payload),
    ]


def _trace_prediction(
    *,
    request: SpecialistRequest,
    prediction: SpecialistPrediction,
    model_id: str | None,
    promoted_model_available: bool,
    fallback_required: bool,
    fallback_used: bool,
    decision_source: str,
) -> None:
    if request.context.get("trace_enabled", True) is False:
        return
    append_specialist_trace(
        request_type="prediction",
        task_type=prediction.label,
        recommended_specialist=prediction.specialist,
        specialist_name=prediction.specialist,
        confidence=prediction.confidence,
        model_id=model_id,
        promoted_model_available=promoted_model_available,
        fallback_required=fallback_required,
        fallback_used=fallback_used,
        decision_source=decision_source,
        safety_notes=[
            "Recommendation only.",
            "Specialist predictions do not execute tools.",
            "Specialist predictions do not authorize patches or runtime actions.",
            "Rule-based fallback remains available.",
        ],
        input_text=request.text,
        path=request.context.get("trace_store_path"),
    )
