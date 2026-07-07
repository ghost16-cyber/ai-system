from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from backend.app.local_runtime.task_optimizer import classify_task
from backend.app.slm.action_parser import ActionParseError, extract_json_object
from backend.app.slm.runtime_config import get_selected_slm_profile


SAFETY_METADATA = {
    "advisory_only": True,
    "tools_executed": False,
    "patches_applied": False,
    "runtime_authorized": False,
}


class SLMChatRequest(BaseModel):
    message: str = ""
    context: dict[str, Any] = Field(default_factory=dict)


class SLMIntentRequest(BaseModel):
    message: str = ""
    context: dict[str, Any] = Field(default_factory=dict)


def chat_with_slm(message: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
    selected = get_selected_slm_profile()
    profile = selected["profile"]
    compact_context = _compact_context(context or {})
    response = _mock_chat_response(message, compact_context)
    return {
        "message": message,
        "assistant_response": response,
        "source": "mock",
        "selected_profile": profile,
        "backend_available": profile.get("backend") == "mock",
        **SAFETY_METADATA,
    }


def infer_intent_with_slm(
    message: str,
    context: dict[str, Any] | None = None,
    *,
    raw_model_output: str | None = None,
) -> dict[str, Any]:
    if raw_model_output:
        parsed = _parse_intent_output(raw_model_output)
        if parsed is not None:
            return parsed
    return deterministic_intent(message, context or {})


def deterministic_intent(message: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
    task_type = classify_task(message)
    intent = {
        "code_repair": "code_repair",
        "local_slm": "local_slm_assistance",
        "rag": "retrieval_context",
        "training": "model_training_guidance",
        "classical_ml": "classical_ml_guidance",
    }.get(task_type, "general_assistance")
    return {
        "intent": intent,
        "task_type": task_type,
        "confidence": 0.72 if task_type != "general" else 0.45,
        "entities": _extract_entities(message),
        "recommended_next_step": "Use deterministic Astra policy and specialist routing before any action.",
        "safety_notes": [
            "SLM intent is advisory only.",
            "No tools are executed.",
            "No patches are applied.",
            "No runtime action is authorized.",
        ],
        "source": "mock",
        **SAFETY_METADATA,
    }


def _parse_intent_output(raw: str) -> dict[str, Any] | None:
    try:
        payload = extract_json_object(raw)
    except ActionParseError:
        return None
    intent = payload.get("intent")
    task_type = payload.get("task_type")
    confidence = payload.get("confidence", 0.5)
    if not isinstance(intent, str) or not isinstance(task_type, str):
        return None
    if not isinstance(confidence, (int, float)):
        return None
    entities = payload.get("entities", {})
    return {
        "intent": intent,
        "task_type": task_type,
        "confidence": max(0.0, min(1.0, float(confidence))),
        "entities": entities if isinstance(entities, dict) else {},
        "recommended_next_step": str(payload.get("recommended_next_step") or "Use deterministic policy."),
        "safety_notes": payload.get("safety_notes")
        if isinstance(payload.get("safety_notes"), list)
        else ["SLM intent is advisory only."],
        "source": "local_slm",
        **SAFETY_METADATA,
    }


def _mock_chat_response(message: str, context: str) -> str:
    prefix = "I can help reason about this safely."
    if context:
        return f"{prefix} I found local context to consider, but it is advisory only. Request: {message[:240]}"
    return f"{prefix} Specialists and runtime gates remain authoritative. Request: {message[:240]}"


def _compact_context(context: dict[str, Any]) -> str:
    try:
        return json.dumps(context, sort_keys=True)[:1200]
    except TypeError:
        return str(context)[:1200]


def _extract_entities(message: str) -> dict[str, Any]:
    lowered = message.lower()
    return {
        "mentions_cuda": "cuda" in lowered or "gpu" in lowered,
        "mentions_rag": "rag" in lowered or "retrieval" in lowered,
        "mentions_tests": "pytest" in lowered or "test" in lowered,
        "mentions_security": any(token in lowered for token in ("secret", "token", "credential", "security")),
    }
