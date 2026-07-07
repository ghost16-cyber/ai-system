from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.app.local_runtime import (
    build_execution_profile,
    build_runtime_context,
    validate_task_plan,
)
from backend.app.local_runtime.task_optimizer import classify_task
from backend.app.rag import context_service as rag_context_service
from backend.app.schemas.api import ChatRunRequest, ChatRunResponse
from backend.app.slm import gateway as slm_gateway
from backend.app.specialists.specialist_router import route_specialist_task


def run_chat_workflow(
    request: ChatRunRequest,
    *,
    workspace_root: str | Path,
) -> ChatRunResponse:
    created_at = datetime.now(timezone.utc)
    run_id = str(uuid4())
    conversation_id = request.conversation_id or str(uuid4())
    trace: list[dict[str, Any]] = [
        _trace(
            "accepted",
            "Chat request accepted",
            "Astra created one consolidated chat run for this message.",
        )
    ]

    route = _route_message(request.message, trace)
    rag_results = _retrieve_context(
        request.message,
        use_rag=request.use_rag,
        workspace_root=workspace_root,
        trace=trace,
    )
    runtime_context, validation, profile = _runtime_decision(
        request.message,
        trace=trace,
        workspace_root=workspace_root,
    )
    slm_response = _try_slm(
        request.message,
        trace=trace,
        route=route,
        rag_results=rag_results,
        validation=validation,
        profile=profile,
        safety_mode=request.safety_mode,
    )
    assistant_response = _assistant_response(
        request.message,
        route=route,
        rag_results=rag_results,
        validation=validation,
        profile=profile,
        slm_response=slm_response,
        runtime_context=runtime_context,
    )

    return ChatRunResponse(
        run_id=run_id,
        conversation_id=conversation_id,
        user_message=request.message,
        assistant_response=assistant_response,
        selected_specialist=str(route.get("recommended_specialist") or "general_specialist"),
        intent=str(route.get("task_type") or classify_task(request.message)),
        confidence=_float(route.get("confidence"), 0.0),
        rag_used=bool(rag_results),
        rag_context_count=len(rag_results),
        runtime_decision=_runtime_label(validation, profile),
        safety_decision=validation.decision,
        created_at=created_at,
        trace_summary=trace,
    )


def _route_message(message: str, trace: list[dict[str, Any]]) -> dict[str, Any]:
    try:
        route = route_specialist_task(
            {
                "text": message,
                "use_slm_intent": True,
                "context": {"trace_enabled": False},
            }
        )
        trace.append(
            _trace(
                "specialist",
                "Specialist selected",
                f"{route.get('recommended_specialist')} selected with {round(_float(route.get('confidence')) * 100)}% confidence.",
                {
                    "intent": route.get("task_type"),
                    "fallback_required": route.get("fallback_required"),
                },
            )
        )
        return route
    except Exception as error:  # pragma: no cover - exercised through endpoint tests
        trace.append(
            _trace(
                "specialist",
                "Specialist routing unavailable",
                f"Deterministic fallback route used: {error}",
                {"fallback_required": True},
                status="warning",
            )
        )
        return {
            "task_type": classify_task(message),
            "recommended_specialist": "general_specialist",
            "confidence": 0.2,
            "fallback_required": True,
        }


def _retrieve_context(
    message: str,
    *,
    use_rag: bool,
    workspace_root: str | Path,
    trace: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not use_rag:
        trace.append(
            _trace("rag", "RAG skipped", "RAG was disabled for this chat request.")
        )
        return []
    try:
        search = rag_context_service.rag_search(workspace_root, query=message, limit=4)
        results = search.get("results") if isinstance(search, dict) else []
        if not isinstance(results, list):
            results = []
        trace.append(
            _trace(
                "rag",
                "RAG context retrieved",
                f"{len(results)} local context item(s) were retrieved.",
                {"count": len(results)},
            )
        )
        return [item for item in results if isinstance(item, dict)]
    except Exception as error:
        trace.append(
            _trace(
                "rag",
                "RAG unavailable",
                f"RAG failed gracefully: {error}",
                {"count": 0},
                status="warning",
            )
        )
        return []


def _runtime_decision(
    message: str,
    *,
    trace: list[dict[str, Any]],
    workspace_root: str | Path,
):
    context = build_runtime_context(task=message, workspace_root=workspace_root)
    plan = _default_plan(classify_task(message))
    validation = validate_task_plan(
        task=message,
        requested_plan=plan,
        runtime_context=context,
    )
    profile = None
    if validation.decision != "block":
        try:
            profile = build_execution_profile(
                task=message,
                runtime_context=context,
                active_runtime_plan=validation.recommended_plan or plan,
            )
        except Exception as error:
            trace.append(
                _trace(
                    "runtime",
                    "Runtime profile unavailable",
                    f"Safety decision was made, but no profile was compiled: {error}",
                    status="warning",
                )
            )
    trace.append(
        _trace(
            "safety",
            f"Plan {validation.decision}",
            validation.reason,
            {
                "runtime": getattr(profile, "runtime", None),
                "device": getattr(profile, "device", None),
                "blocked_signals": validation.blocked_signals,
            },
            status=(
                "passed"
                if validation.decision == "allow"
                else "blocked"
                if validation.decision == "block"
                else "warning"
            ),
        )
    )
    return context, validation, profile


def _try_slm(
    message: str,
    *,
    trace: list[dict[str, Any]],
    route: dict[str, Any],
    rag_results: list[dict[str, Any]],
    validation,
    profile,
    safety_mode: str,
) -> dict[str, Any] | None:
    try:
        response = slm_gateway.chat_with_slm(
            message,
            {
                "selected_specialist": route.get("recommended_specialist"),
                "rag_context": rag_context_service.compact_context(rag_results),
                "runtime_decision": _runtime_label(validation, profile),
                "safety_decision": validation.decision,
                "safety_mode": safety_mode,
            },
        )
    except Exception as error:
        trace.append(
            _trace(
                "slm",
                "SLM unavailable",
                f"Deterministic assistant response used: {error}",
                status="warning",
            )
        )
        return None

    if response.get("source") == "mock":
        trace.append(
            _trace(
                "slm",
                "SLM gateway fallback",
                "The selected SLM gateway reported fallback mode, so Astra generated a deterministic grounded response.",
                status="warning",
            )
        )
        return None

    trace.append(
        _trace("slm", "SLM response generated", "The selected SLM gateway returned a live response.")
    )
    return response


def _assistant_response(
    message: str,
    *,
    route: dict[str, Any],
    rag_results: list[dict[str, Any]],
    validation,
    profile,
    slm_response: dict[str, Any] | None,
    runtime_context,
) -> str:
    if slm_response and slm_response.get("assistant_response"):
        return str(slm_response["assistant_response"])

    specialist = route.get("recommended_specialist") or "general_specialist"
    intent = route.get("task_type") or classify_task(message)
    decision = validation.decision
    runtime = _runtime_label(validation, profile)
    rag_sentence = (
        f"I used {len(rag_results)} retrieved project context item(s)."
        if rag_results
        else "I did not use retrieved project context for this answer."
    )
    context_hint = _context_hint(rag_results)
    next_step = _next_step(str(specialist), str(intent), decision)
    hardware = getattr(runtime_context, "hardware", None)
    gpu = getattr(getattr(hardware, "gpu", None), "name", "") if hardware else ""

    return (
        f"I routed this to {specialist} for a {intent} request. "
        f"Safety decision: {decision}. Runtime: {runtime}. {rag_sentence} "
        f"{context_hint}"
        f"The safest useful next step is: {next_step} "
        f"No files were changed, no tools were executed from chat, and no destructive action was authorized."
        + (f" Detected runtime hardware: {gpu}." if gpu else "")
    )


def _context_hint(rag_results: list[dict[str, Any]]) -> str:
    if not rag_results:
        return ""
    paths = [
        str(item.get("path"))
        for item in rag_results[:3]
        if item.get("path")
    ]
    if not paths:
        return ""
    return f"Relevant local context came from: {', '.join(paths)}. "


def _next_step(specialist: str, intent: str, decision: str) -> str:
    if decision == "block":
        return "revise the request into a read-only plan before doing anything else."
    if specialist == "bug_triage_specialist":
        return "run read-only inspection first, then choose the smallest test-backed fix proposal."
    if specialist == "rag_specialist" or intent == "rag":
        return "check the retrieved sources, then build a compact retrieval plan before changing code."
    if specialist == "runtime_specialist":
        return "confirm CUDA, VRAM, and fallback constraints before selecting a model or batch size."
    if specialist == "safety_specialist":
        return "keep secrets and credentials out of prompts and use only read-only verification."
    if specialist == "training_specialist":
        return "start with a tiny dry-run and low-memory settings before any long training job."
    return "inspect the relevant context and produce a preview plan before authorizing any action."


def _default_plan(task_type: str) -> dict[str, Any]:
    if task_type == "rag":
        return {"strategy": "embedding_retrieval", "embedding_workflow": True}
    if task_type == "pytorch_training":
        return {"strategy": "pytorch_training", "model_size_billion_params": 1}
    if task_type == "classical_ml":
        return {"strategy": "sklearn_pipeline", "requires_gpu": False, "device": "cpu"}
    return {"strategy": "local_inference", "model_size_billion_params": 3}


def _runtime_label(validation, profile) -> str:
    if validation.decision == "block":
        return "blocked"
    if profile is None:
        return "fallback"
    return f"{profile.runtime}/{profile.device}"


def _trace(
    phase: str,
    title: str,
    detail: str,
    data: dict[str, Any] | None = None,
    *,
    status: str = "passed",
) -> dict[str, Any]:
    return {
        "phase": phase,
        "title": title,
        "detail": detail,
        "status": status,
        **({"data": data} if data else {}),
    }


def _float(value: Any, fallback: float = 0.0) -> float:
    if isinstance(value, (int, float)):
        return max(0.0, min(1.0, float(value)))
    return fallback
