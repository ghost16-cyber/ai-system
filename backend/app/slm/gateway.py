from __future__ import annotations

import json
import socket
import time
from typing import Any

from pydantic import BaseModel, Field

from backend.app.local_runtime.task_optimizer import classify_task
from backend.app.local_ai.config import load_local_ai_configuration
from backend.app.local_ai.provider import OllamaProviderClient, ProviderClientError
from backend.app.slm.action_parser import ActionParseError, extract_json_object
from backend.app.slm.runtime_config import get_selected_slm_profile
from backend.app.slm.model_registry import build_ollama_client


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


def _load_gateway_config() -> dict[str, Any]:
    configuration = load_local_ai_configuration()
    return {
        "enabled": configuration.generation_enabled,
        "base_url": configuration.endpoint_identity,
        "model": configuration.coder_model,
        "timeout_seconds": configuration.generation_timeout_seconds,
        "connection_timeout_seconds": configuration.connection_timeout_seconds,
        "configuration_schema_version": configuration.schema_version,
    }


def _check_ollama_reachable(base_url: str, timeout: int) -> tuple[bool, list[str]]:
    """Check if Ollama is reachable and return a list of available models."""
    try:
        inspection = OllamaProviderClient(base_url).inspect(timeout_seconds=timeout)
        return True, list(inspection.installed_models)
    except ProviderClientError:
        return False, []


def get_slm_gateway_status() -> dict[str, Any]:
    config = _load_gateway_config()
    selected = get_selected_slm_profile()
    profile = selected["profile"]
    selected_model = profile.get("model_name") or config["model"]
    reachable, available_models = _check_ollama_reachable(
        config["base_url"], timeout=config["connection_timeout_seconds"]
    )
    return {
        "enabled": config["enabled"],
        "base_url": config["base_url"],
        "configured_model": selected_model,
        "selected_model": selected_model,
        "provider": profile.get("backend", "unknown"),
        "selected_profile_id": selected["selected_profile_id"],
        "reachable": reachable,
        "available_models": available_models,
    }


def chat_with_slm(message: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
    config = _load_gateway_config()
    selected = get_selected_slm_profile()
    profile = selected["profile"]
    safe_context = {
        key: value for key, value in (context or {}).items() if key != "prompt"
    }
    compact_context = _compact_context(safe_context)

    def fallback(reason: str) -> dict[str, Any]:
        return _fallback_response(
            message,
            compact_context,
            profile,
            reason=reason,
            model=profile.get("model_name") or config["model"],
        )

    if not config["enabled"]:
        return fallback("disabled_by_config")

    if profile.get("backend") != "ollama":
        return fallback("profile_backend_not_ollama")

    target_model = profile.get("model_name") or config["model"]
    reachable, available_models = _check_ollama_reachable(
        config["base_url"], timeout=config["connection_timeout_seconds"]
    )

    if not reachable:
        return fallback("ollama_unreachable")

    if not target_model:
        return fallback("model_missing")

    if not _model_available(str(target_model), available_models):
        return fallback("model_missing")

    client = build_ollama_client(
        model=target_model,
        base_url=config["base_url"],
        timeout_seconds=config["timeout_seconds"],
    )

    explicit_prompt = (context or {}).get("prompt")
    prompt = (
        explicit_prompt
        if isinstance(explicit_prompt, str) and explicit_prompt.strip()
        else f"{message}\n\nContext:\n{compact_context}"
        if compact_context
        else message
    )

    start_time = time.monotonic()
    try:
        response_text = client.generate(prompt)
    except (TimeoutError, socket.timeout):
        return fallback("timeout")
    except Exception:
        return fallback("provider_error")

    latency_ms = int((time.monotonic() - start_time) * 1000)

    if not response_text:
        return fallback("invalid_response")

    return {
        "message": message,
        "assistant_response": response_text,
        "source": "local_slm",
        "provider": "ollama",
        "model": target_model,
        "used_real_slm": True,
        "fallback_reason": None,
        "latency_ms": latency_ms,
        "selected_profile": profile,
        "backend_available": True,
        **SAFETY_METADATA,
    }


def _model_available(target_model: str, available_models: list[str]) -> bool:
    return target_model in available_models


def _fallback_response(
    message: str,
    context: str,
    profile: dict[str, Any],
    *,
    reason: str,
    model: str | None,
) -> dict[str, Any]:
    response = _mock_chat_response(message, context)
    return {
        "message": message,
        "assistant_response": response,
        "source": "mock",
        "provider": "fallback",
        "model": model,
        "used_real_slm": False,
        "fallback_reason": reason,
        "latency_ms": None,
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
