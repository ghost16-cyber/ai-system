from __future__ import annotations

from typing import Any

from .model_store import list_specialist_models
from .schemas import SpecialistRequest
from .trace_store import append_specialist_trace


_ROUTE_RULES = [
    (
        "safety_specialist",
        ["security", "unsafe", "secret", "credential", "token", "permission", "policy"],
        "Security or safety-sensitive request.",
    ),
    (
        "runtime_specialist",
        ["cuda", "gpu", "vram", "runtime", "hardware", "memory", "oom", "device"],
        "Runtime or hardware issue.",
    ),
    (
        "bug_triage_specialist",
        ["bug", "error", "traceback", "exception", "failing", "failed", "fix", "repair"],
        "Bug report or repair request.",
    ),
    (
        "code_quality_specialist",
        ["refactor", "lint", "style", "cleanup", "quality", "typing", "types"],
        "Code quality or maintainability request.",
    ),
    (
        "rag_specialist",
        ["rag", "retrieval", "embedding", "faiss", "vector", "search"],
        "Retrieval or embedding workflow request.",
    ),
    (
        "training_specialist",
        ["train", "fine-tune", "finetune", "pytorch", "batch", "gradient", "epoch"],
        "Training workflow request.",
    ),
]


def route_specialist_task(
    payload: SpecialistRequest | dict,
    model_dir: str | None = None,
) -> dict[str, Any]:
    request = payload if isinstance(payload, SpecialistRequest) else SpecialistRequest.model_validate(payload)
    slm_intent_summary: dict[str, Any] | None = None
    slm_warning: str | None = None
    if request.use_slm_intent:
        try:
            from backend.app.slm.gateway import infer_intent_with_slm

            slm_intent_summary = infer_intent_with_slm(request.text, request.context)
        except Exception as error:
            slm_warning = f"SLM intent unavailable; deterministic routing used: {error}"
    text = request.text.lower()
    best = {
        "recommended_specialist": "general_specialist",
        "confidence": 0.35,
        "matched_features": [],
        "reason": "No specific specialist route matched; use a general advisory specialist.",
    }

    for specialist, keywords, reason in _ROUTE_RULES:
        matches = [keyword for keyword in keywords if keyword in text]
        if not matches:
            continue
        confidence = min(0.95, 0.55 + (0.1 * len(matches)))
        if confidence > best["confidence"]:
            best = {
                "recommended_specialist": specialist,
                "confidence": confidence,
                "matched_features": matches,
                "reason": reason,
            }

    active_model = _find_promoted_model(
        best["recommended_specialist"],
        model_dir or request.context.get("model_dir"),
    )
    promoted_model_available = active_model is not None
    fallback_required = not promoted_model_available
    deterministic_decision = {
        "task_type": best["recommended_specialist"].replace("_specialist", ""),
        "recommended_specialist": best["recommended_specialist"],
        "confidence": best["confidence"],
        "model_id": active_model.get("model_id") if active_model else None,
        "promoted_model_available": promoted_model_available,
        "fallback_required": fallback_required,
    }
    result = {
        "router": "specialist_router",
        "task_type": deterministic_decision["task_type"],
        **best,
        "promoted_model_available": promoted_model_available,
        "model_id": active_model.get("model_id") if active_model else None,
        "fallback_required": fallback_required,
        "slm_intent_used": request.use_slm_intent and slm_intent_summary is not None,
        "slm_intent_summary": slm_intent_summary,
        "slm_warning": slm_warning,
        "deterministic_decision": deterministic_decision,
        "final_decision": deterministic_decision,
        "safety_notes": [
            "Recommendation only.",
            "No tools are executed.",
            "No patch or runtime action is authorized by this router.",
            "Rule-based fallback remains available.",
        ],
        "candidate_specialists": [rule[0] for rule in _ROUTE_RULES] + ["general_specialist"],
        "advisory_only": True,
        "execution_allowed": False,
    }
    if request.context.get("trace_enabled", True) is not False:
        append_specialist_trace(
            request_type="route",
            task_type=result["task_type"],
            recommended_specialist=result["recommended_specialist"],
            confidence=result["confidence"],
            model_id=result["model_id"],
            promoted_model_available=promoted_model_available,
            fallback_required=fallback_required,
            fallback_used=fallback_required,
            decision_source="router",
            safety_notes=result["safety_notes"],
            input_text=request.text,
            extra={
                "slm_intent_used": result["slm_intent_used"],
                "slm_intent_task_type": (
                    slm_intent_summary.get("task_type")
                    if isinstance(slm_intent_summary, dict)
                    else None
                ),
            },
            path=request.context.get("trace_store_path"),
        )
    return result


def _find_promoted_model(
    specialist_name: str,
    model_dir: str | None = None,
) -> dict[str, Any] | None:
    models = list_specialist_models(model_dir)["models"]
    for model in models:
        if (
            model.get("specialist") == specialist_name
            and model.get("lifecycle_status") == "promoted"
            and model.get("valid") is True
        ):
            return model
    return None
