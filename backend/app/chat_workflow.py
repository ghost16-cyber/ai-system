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


ASTRA_CAPABILITY_SUMMARY = (
    "Astra is a local prototype assistant for this repository.",
    "It can route a chat request to a specialist such as code, RAG, runtime, safety, or training.",
    "It can use read-only RAG over safe local project files when the question needs project context.",
    "It can check backend/runtime health, selected SLM profile, RAG status, tools, jobs, and recent runs.",
    "It can explain, inspect, and draft safe plans; destructive actions remain blocked or require explicit confirmation.",
)

RAG_STOPWORDS = {
    "about",
    "again",
    "answer",
    "astra",
    "bullet",
    "bullets",
    "can",
    "currently",
    "detail",
    "does",
    "explain",
    "give",
    "hello",
    "help",
    "hi",
    "please",
    "simple",
    "system",
    "thanks",
    "that",
    "this",
    "what",
    "with",
    "would",
    "you",
    "your",
}


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
        route=route,
        trace=trace,
    )
    runtime_context, validation, profile = _runtime_decision(
        request.message,
        trace=trace,
        workspace_root=workspace_root,
    )
    slm_result = _call_slm_gateway(
        request.message,
        trace=trace,
        route=route,
        rag_results=rag_results,
        validation=validation,
        profile=profile,
        safety_mode=request.safety_mode,
    )
    slm_response_for_text = slm_result if slm_result.get("used_real_slm") else None
    assistant_response = _assistant_response(
        request.message,
        route=route,
        rag_results=rag_results,
        validation=validation,
        profile=profile,
        slm_response=slm_response_for_text,
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
        used_real_slm=bool(slm_result.get("used_real_slm")),
        slm_provider=str(slm_result.get("provider") or "fallback"),
        slm_model=(
            str(slm_result.get("model"))
            if slm_result.get("model") is not None
            else None
        ),
        slm_fallback_reason=(
            str(slm_result.get("fallback_reason"))
            if slm_result.get("fallback_reason") is not None
            else None
        ),
        slm_latency_ms=(
            int(slm_result["latency_ms"])
            if isinstance(slm_result.get("latency_ms"), int)
            else None
        ),
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
    route: dict[str, Any],
    trace: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not use_rag:
        trace.append(
            _rag_trace("disabled", "RAG was disabled for this chat request.")
        )
        return []
    gate = _rag_gate_reason(message, route)
    if gate:
        trace.append(_rag_trace(gate, f"RAG skipped: {gate.replace('_', ' ')}."))
        return []
    try:
        search = rag_context_service.rag_search(workspace_root, query=message, limit=4)
        results = search.get("results") if isinstance(search, dict) else []
        if not isinstance(results, list):
            results = []
        filtered, rejected = _filter_relevant_rag_results(message, route, results)
        if not filtered:
            trace.append(
                _rag_trace(
                    "low_relevance",
                    "RAG skipped: low relevance.",
                    {"retrieved_count": len(results), "rejected_count": rejected},
                )
            )
            return []
        trace.append(
            _trace(
                "rag",
                "RAG context retrieved",
                f"{len(filtered)} relevant local context item(s) were retrieved.",
                {
                    "count": len(filtered),
                    "retrieved_count": len(results),
                    "rejected_count": rejected,
                    "reason": "project_context_relevant",
                },
            )
        )
        return filtered
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


def _call_slm_gateway(
    message: str,
    *,
    trace: list[dict[str, Any]],
    route: dict[str, Any],
    rag_results: list[dict[str, Any]],
    validation,
    profile,
    safety_mode: str,
) -> dict[str, Any]:
    prompt = build_chat_prompt(
        message,
        route=route,
        rag_results=rag_results,
        validation=validation,
        profile=profile,
    )
    try:
        response = slm_gateway.chat_with_slm(
            message,
            {
                "prompt": prompt,
                "selected_specialist": route.get("recommended_specialist"),
                "rag_context": rag_context_service.compact_context(rag_results),
                "runtime_decision": _runtime_label(validation, profile),
                "safety_decision": validation.decision,
                "safety_mode": safety_mode,
            },
        )
    except Exception as error:
        response = {
            "used_real_slm": False,
            "provider": "fallback",
            "fallback_reason": f"gateway_exception:{error}",
        }
        trace.append(
            _trace(
                "slm",
                "SLM unavailable",
                f"Deterministic assistant response used: {error}",
                data={"provider": "fallback", "used_real_slm": False},
                status="warning",
            )
        )
        return response

    used_real = response.get("used_real_slm", False)
    provider = response.get("provider", "fallback")
    model = response.get("model")
    reason = response.get("fallback_reason")

    if used_real:
        title = "SLM response generated"
        detail = f"SLM: {provider}/{model}"
        status = "passed"
    else:
        title = "SLM gateway fallback"
        detail = f"SLM: fallback ({reason})"
        status = "warning"

    trace.append(
        _trace(
            "slm",
            title,
            detail,
            data={
                "provider": provider,
                "model": model,
                "used_real_slm": used_real,
                "fallback_reason": reason,
                "latency_ms": response.get("latency_ms"),
            },
            status=status,
        )
    )
    return response


def build_chat_prompt(
    message: str,
    *,
    route: dict[str, Any],
    rag_results: list[dict[str, Any]],
    validation,
    profile,
) -> str:
    capability_lines = "\n".join(f"- {line}" for line in ASTRA_CAPABILITY_SUMMARY)
    rag_block = (
        rag_context_service.compact_context(rag_results)
        if rag_results
        else "No RAG context is attached for this request."
    )
    return (
        "You are Astra, a local prototype assistant UI backed by this backend.\n"
        "Answer the user's actual question first. Keep the answer concise unless the user asks for detail.\n"
        "Do not invent capabilities. RAG context is optional supporting evidence, not the main instruction.\n"
        "If RAG context conflicts with the user's question, ignore the RAG context and answer from the request and capability summary.\n"
        "Do not claim files were changed, patches applied, or tools executed from chat.\n\n"
        "Astra capability summary:\n"
        f"{capability_lines}\n\n"
        "Routing and safety context:\n"
        f"- Selected specialist: {route.get('recommended_specialist') or 'general_specialist'}\n"
        f"- Intent: {route.get('task_type') or classify_task(message)}\n"
        f"- Confidence: {round(_float(route.get('confidence')) * 100)}%\n"
        f"- Safety decision: {validation.decision}\n"
        f"- Runtime decision: {_runtime_label(validation, profile)}\n\n"
        "Optional RAG context:\n"
        f"{rag_block}\n\n"
        "User message:\n"
        f"{message}\n"
    )


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

    if _is_greeting(message):
        return "Hi. I can help inspect, explain, plan, or draft safe next steps for the Astra system."

    if _is_system_meta_question(message.lower()):
        count = _requested_bullet_count(message) or 5
        bullets = list(ASTRA_CAPABILITY_SUMMARY)[:count]
        return "\n".join(f"- {bullet}" for bullet in bullets)

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


def _rag_trace(reason: str, detail: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = {"count": 0, "reason": reason, **(data or {})}
    return _trace("rag", f"RAG skipped: {reason.replace('_', ' ')}", detail, payload, status="warning")


def _rag_gate_reason(message: str, route: dict[str, Any]) -> str | None:
    lowered = message.strip().lower()
    if _is_greeting(message):
        return "greeting"
    if _is_system_meta_question(lowered):
        return "system_meta_question"
    if not _looks_project_or_coding_question(lowered, route):
        return "low_relevance"
    return None


def _is_greeting(message: str) -> bool:
    compact = message.strip().lower().strip(" .!?")
    return compact in {"hi", "hello", "hey", "thanks", "thank you", "thx"}


def _is_system_meta_question(lowered: str) -> bool:
    meta_phrases = (
        "what can astra do",
        "what this astra system can",
        "what does astra do",
        "what is astra",
        "explain astra",
        "about astra",
        "your capabilities",
        "its capabilities",
        "currently do",
    )
    return any(phrase in lowered for phrase in meta_phrases) and not re_contains_code_topic(lowered)


def _looks_project_or_coding_question(lowered: str, route: dict[str, Any]) -> bool:
    task_type = str(route.get("task_type") or "")
    if task_type in {"code_repair", "rag", "pytorch_training", "classical_ml"}:
        return True
    project_terms = (
        "backend",
        "bug",
        "code",
        "endpoint",
        "frontend",
        "history",
        "implementation",
        "phase",
        "project",
        "pytest",
        "rag",
        "repo",
        "runtime",
        "test",
        "trace",
        "workflow",
    )
    return any(term in lowered for term in project_terms)


def re_contains_code_topic(lowered: str) -> bool:
    return any(term in lowered for term in ("code", "repo", "file", "backend", "frontend", "test", "bug"))


def _filter_relevant_rag_results(
    message: str,
    route: dict[str, Any],
    results: list[Any],
) -> tuple[list[dict[str, Any]], int]:
    query_terms = _topic_terms(message)
    intent = str(route.get("task_type") or classify_task(message))
    filtered: list[dict[str, Any]] = []
    rejected = 0
    for item in results:
        if not isinstance(item, dict):
            rejected += 1
            continue
        score = _float(item.get("score"), 0.0)
        haystack = " ".join(
            str(item.get(key) or "")
            for key in ("path", "title", "source", "snippet")
        ).lower()
        overlap = query_terms & _topic_terms(haystack)
        intent_match = _rag_item_matches_intent(haystack, intent)
        if score >= 1.0 and (overlap or intent_match):
            filtered.append(item)
        else:
            rejected += 1
    return filtered[:4], rejected


def _rag_item_matches_intent(haystack: str, intent: str) -> bool:
    if intent == "rag":
        return any(term in haystack for term in ("rag", "retrieval", "embedding", "context"))
    if intent == "code_repair":
        return any(term in haystack for term in ("test", "bug", "fix", "patch", "code"))
    if intent == "pytorch_training":
        return any(term in haystack for term in ("train", "model", "cuda", "gpu", "dataset"))
    if intent == "classical_ml":
        return any(term in haystack for term in ("sklearn", "classifier", "regression", "tabular"))
    return False


def _topic_terms(text: str) -> set[str]:
    normalized = "".join(character.lower() if character.isalnum() else " " for character in text)
    return {
        token
        for token in normalized.split()
        if len(token) > 2 and token not in RAG_STOPWORDS
    }


def _requested_bullet_count(message: str) -> int | None:
    normalized = "".join(character if character.isdigit() else " " for character in message)
    for token in normalized.split():
        if token.isdigit():
            count = int(token)
            if 1 <= count <= 8:
                return count
    return None


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
