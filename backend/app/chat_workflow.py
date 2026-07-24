from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from backend.app.chat_runtime.prompts import ASTRA_CAPABILITY_SUMMARY
from backend.app.chat_runtime.service import CanonicalChatRuntimeService
from backend.app.local_runtime import (
    build_execution_profile,
    build_runtime_context,
    validate_task_plan,
)
from backend.app.local_runtime.task_optimizer import classify_task
from backend.app.project_control.contracts import content_hash
from backend.app.rag.corpus_retrieval import (
    CorpusRetrievalResult,
    retrieve_corpus_context,
)
from backend.app.runtime.contracts import RuntimeReadiness
from backend.app.schemas.api import ChatRunRequest, ChatRunResponse
from backend.app.specialists.specialist_router import route_specialist_task


def run_chat_workflow(
    request: ChatRunRequest,
    *,
    workspace_root: str | Path,
    chat_runtime: CanonicalChatRuntimeService,
    chat_request_id: str,
    previous_turns: list[ChatRunResponse] | None = None,
    event_sink: Callable[[dict[str, Any]], None] | None = None,
    runtime_readiness: RuntimeReadiness | None = None,
    lineage_sink: Callable[[Any], None] | None = None,
) -> ChatRunResponse:
    created_at = datetime.now(timezone.utc)
    run_id = str(uuid4())
    conversation_id = request.conversation_id or str(uuid4())
    prior_turns = previous_turns or []
    memory_summary = _build_memory_summary(prior_turns)
    memory_used = bool(memory_summary) and _should_use_memory(request.message, prior_turns)
    _emit_event(
        event_sink,
        "run_started",
        {
            "run_id": run_id,
            "conversation_id": conversation_id,
            "user_message": request.message,
            "created_at": created_at.isoformat(),
            "memory_used": memory_used,
        },
    )
    trace: list[dict[str, Any]] = [
        _trace(
            "accepted",
            "Chat request accepted",
            "Astra created one consolidated chat run for this message.",
        )
    ]
    if runtime_readiness is not None and not runtime_readiness.ready:
        # Readiness gate only: chat still runs and answers using the exact
        # same retrieval/generation flow below -- this only reports reduced
        # capability, it never blocks the response.
        trace.append(
            _trace(
                "runtime_degraded",
                "Runtime is degraded",
                "One or more runtime subsystems are not fully ready; answering with reduced capability.",
                {"blocking_reasons": list(runtime_readiness.blocking_reasons)},
                status="warning",
            )
        )
    trace.append(
        _trace(
            "memory",
            "Conversation memory used" if memory_used else "Conversation memory not used",
            (
                "Local conversation summary was attached to help answer this follow-up."
                if memory_used
                else "No useful prior conversation context was attached for this message."
            ),
            {
                "conversation_id": conversation_id,
                "previous_turn_count": len(prior_turns),
                "memory_used": memory_used,
            },
            status="passed",
        )
    )

    route = _route_message(request.message, trace)
    _emit_event(
        event_sink,
        "specialist_selected",
        {
            "run_id": run_id,
            "conversation_id": conversation_id,
            "selected_specialist": str(route.get("recommended_specialist") or "general_specialist"),
            "intent": str(route.get("task_type") or classify_task(request.message)),
            "confidence": _float(route.get("confidence"), 0.0),
        },
    )
    project_run_id = request.project_run_id
    use_rag_effective, rag_gate_reason = _rag_gate(
        request.message, use_rag=request.use_rag, route=route, project_run_id=project_run_id,
    )
    corpus_enabled = (
        request.use_rag
        if request.use_corpus is None
        else request.use_corpus
    )
    corpus_retrieval = retrieve_corpus_context(
        request.message,
        workspace_root=workspace_root,
        enabled=corpus_enabled,
    )
    trace.append(_corpus_retrieval_trace(corpus_retrieval))
    runtime_context, validation, profile = _runtime_decision(
        request.message,
        trace=trace,
        workspace_root=workspace_root,
    )
    _emit_event(
        event_sink,
        "safety_completed",
        {
            "run_id": run_id,
            "conversation_id": conversation_id,
            "safety_decision": validation.decision,
            "runtime_decision": _runtime_label(validation, profile),
        },
    )
    request_fingerprint = content_hash(
        request.model_dump(mode="json", exclude={"request_id"})
    )
    answer = chat_runtime.answer(
        chat_request_id=chat_request_id,
        chat_run_id=run_id,
        conversation_id=conversation_id,
        message=request.message,
        project_run_id=project_run_id,
        specialist=str(route.get("recommended_specialist") or "general_specialist"),
        intent=str(route.get("task_type") or classify_task(request.message)),
        confidence=_float(route.get("confidence"), 0.0),
        safety_decision=validation.decision,
        runtime_decision=_runtime_label(validation, profile),
        memory_summary=memory_summary if memory_used else None,
        use_rag=use_rag_effective,
        request_fingerprint=request_fingerprint,
        corpus_context=corpus_retrieval.prompt_context or None,
    )
    if lineage_sink is not None:
        lineage_sink(answer.lineage)
    retrieval = answer.lineage.retrieval
    citations = retrieval.citations if retrieval is not None else ()
    rag_skip_reason = None if retrieval is not None else (rag_gate_reason or "retrieval_unavailable")
    trace.append(_rag_outcome_trace(rag_gate_reason, retrieval))
    trace.append(_generation_trace(answer))
    _emit_event(
        event_sink,
        "rag_completed",
        {
            "run_id": run_id,
            "conversation_id": conversation_id,
            "rag_used": bool(citations),
            "rag_skip_reason": rag_skip_reason,
            "rag_context_count": len(citations),
        },
    )

    assistant_response = answer.assistant_response or _assistant_response(
        request.message,
        route=route,
        citations=citations,
        corpus_retrieval=corpus_retrieval,
        validation=validation,
        profile=profile,
        runtime_context=runtime_context,
        memory_summary=memory_summary if memory_used else None,
        previous_turns=prior_turns,
    )
    for delta in _response_deltas(assistant_response):
        _emit_event(
            event_sink,
            "response_delta",
            {
                "run_id": run_id,
                "conversation_id": conversation_id,
                "delta": delta,
            },
        )

    grounding_metadata = _grounding_metadata_from_citations(
        citations, rag_requested=request.use_rag, skip_reason=rag_skip_reason,
    )
    return ChatRunResponse(
        run_id=run_id,
        conversation_id=conversation_id,
        user_message=request.message,
        assistant_response=assistant_response,
        selected_specialist=str(route.get("recommended_specialist") or "general_specialist"),
        intent=str(route.get("task_type") or classify_task(request.message)),
        confidence=_float(route.get("confidence"), 0.0),
        rag_used=bool(citations),
        rag_skip_reason=rag_skip_reason,
        rag_context_count=len(citations),
        rag_sources=grounding_metadata["rag_sources"],
        source_count=grounding_metadata["source_count"],
        source_paths=grounding_metadata["source_paths"],
        grounding_status=grounding_metadata["grounding_status"],
        corpus_retrieval_used=corpus_retrieval.used,
        corpus_retrieval_skip_reason=corpus_retrieval.skip_reason,
        corpus_context_count=len(corpus_retrieval.sources),
        corpus_sources=corpus_retrieval.sources,
        runtime_decision=_runtime_label(validation, profile),
        safety_decision=validation.decision,
        used_real_slm=answer.used_real_slm,
        slm_provider=answer.provider,
        slm_model=answer.model,
        slm_fallback_reason=answer.fallback_reason,
        slm_latency_ms=answer.latency_ms,
        memory_used=memory_used,
        memory_summary=memory_summary,
        created_at=created_at,
        trace_summary=trace,
        runtime_state=(runtime_readiness.state.value if runtime_readiness is not None else None),
        runtime_ready=(runtime_readiness.ready if runtime_readiness is not None else None),
        runtime_blocking_reasons=(
            list(runtime_readiness.blocking_reasons) if runtime_readiness is not None else []
        ),
        corpus_ready=(runtime_readiness.corpus_valid if runtime_readiness is not None else None),
        retrieval_mode=("canonical_project" if project_run_id is not None else "none"),
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


def _rag_gate(
    message: str,
    *,
    use_rag: bool,
    route: dict[str, Any],
    project_run_id: str | None,
) -> tuple[bool, str | None]:
    """Decide whether project-bound retrieval should be attempted.

    Returns (effective_use_rag, gate_reason). gate_reason is only set when
    effective_use_rag is False, and is exactly the reason retrieval was never
    attempted (as opposed to attempted-and-unavailable, which the caller
    detects separately from the canonical answer's lineage).
    """

    if not use_rag:
        return False, "disabled"
    gate = _rag_gate_reason(message, route)
    if gate:
        return False, gate
    if project_run_id is None:
        return False, "no_canonical_project"
    return True, None


def _rag_outcome_trace(gate_reason: str | None, retrieval: Any) -> dict[str, Any]:
    if retrieval is not None:
        return _trace(
            "rag",
            "Project retrieval used",
            f"{retrieval.evidence_count} project-bound evidence item(s) were retrieved and attached as citations.",
            {
                "count": retrieval.evidence_count,
                "reason": "project_context_relevant",
                "sources": [
                    {
                        "path": citation.relative_path,
                        "start_line": citation.line_start,
                        "end_line": citation.line_end,
                        "citation_label": citation.citation_label,
                    }
                    for citation in retrieval.citations
                ],
            },
        )
    reason = gate_reason or "retrieval_unavailable"
    return _rag_trace(reason, f"RAG skipped: {reason.replace('_', ' ')}.")


def _generation_trace(answer: Any) -> dict[str, Any]:
    if answer.used_real_slm:
        return _trace(
            "slm",
            "Local AI response generated",
            f"Local AI: {answer.provider}/{answer.model}",
            data={"provider": answer.provider, "model": answer.model, "used_real_slm": True},
            status="passed",
        )
    return _trace(
        "slm",
        "Local AI fallback",
        f"Deterministic assistant response used: {answer.fallback_reason}",
        data={
            "provider": answer.provider,
            "used_real_slm": False,
            "fallback_reason": answer.fallback_reason,
        },
        status="warning",
    )


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


def _assistant_response(
    message: str,
    *,
    route: dict[str, Any],
    citations: tuple[Any, ...],
    corpus_retrieval: CorpusRetrievalResult,
    validation,
    profile,
    runtime_context,
    memory_summary: str | None,
    previous_turns: list[ChatRunResponse],
) -> str:
    memory_answer = _memory_followup_answer(message, previous_turns)
    if memory_summary and memory_answer:
        return memory_answer

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
        f"I used {len(citations)} retrieved project context item(s)."
        if citations
        else "I did not use retrieved project context for this answer."
    )
    corpus_sentence = (
        f"I used {len(corpus_retrieval.sources)} persistent corpus context item(s) as read-only references."
        if corpus_retrieval.used
        else "I did not use persistent corpus context for this answer."
    )
    context_hint = _context_hint(citations)
    corpus_hint = _corpus_context_hint(corpus_retrieval)
    next_step = _next_step(str(specialist), str(intent), decision)
    hardware = getattr(runtime_context, "hardware", None)
    gpu = getattr(getattr(hardware, "gpu", None), "name", "") if hardware else ""

    return (
        f"I routed this to {specialist} for a {intent} request. "
        f"Safety decision: {decision}. Runtime: {runtime}. {rag_sentence} {corpus_sentence} "
        f"{context_hint}"
        f"{corpus_hint}"
        f"The safest useful next step is: {next_step} "
        f"No files were changed, no tools were executed from chat, and no destructive action was authorized."
        + (f" Detected runtime hardware: {gpu}." if gpu else "")
    )


def _build_memory_summary(previous_turns: list[ChatRunResponse]) -> str | None:
    if not previous_turns:
        return None
    selected_turns = previous_turns[-4:]
    lines = [
        f"Earlier turns in conversation: {len(previous_turns)}.",
        f"First user message: {_safe_memory_text(previous_turns[0].user_message, 180)}",
    ]
    for index, turn in enumerate(selected_turns, start=1):
        lines.append(
            "Turn "
            f"{index}: user asked '{_safe_memory_text(turn.user_message, 140)}'; "
            f"Astra answered '{_safe_memory_text(turn.assistant_response, 180)}'; "
            f"specialist={turn.selected_specialist}; intent={turn.intent}; "
            f"rag={'used' if turn.rag_used else 'not used'}."
        )
    return _truncate(" ".join(lines), 1200)


def _should_use_memory(message: str, previous_turns: list[ChatRunResponse]) -> bool:
    if not previous_turns:
        return False
    lowered = message.strip().lower()
    if _is_greeting(message):
        return False
    followup_terms = (
        "again",
        "also",
        "continue",
        "earlier",
        "follow up",
        "first",
        "it",
        "last",
        "previous",
        "same",
        "second",
        "that",
        "them",
        "this",
        "those",
        "what did i",
        "what was",
        "you said",
    )
    return any(term in lowered for term in followup_terms)


def _memory_followup_answer(
    message: str,
    previous_turns: list[ChatRunResponse],
) -> str | None:
    if not previous_turns:
        return None
    lowered = message.lower()
    if "what did i ask" in lowered or "first" in lowered and "ask" in lowered:
        return (
            "Earlier in this conversation, your first message was: "
            f"{previous_turns[0].user_message}"
        )
    if "previous" in lowered or "earlier" in lowered or "last" in lowered:
        latest = previous_turns[-1]
        return (
            "The previous turn was about: "
            f"{latest.user_message} "
            f"I answered with: {_truncate(latest.assistant_response, 260)}"
        )
    return None


def _safe_memory_text(text: str, limit: int) -> str:
    redacted = text
    lowered = redacted.lower()
    sensitive_terms = ("password", "token", "secret", "api key", "credential")
    if any(term in lowered for term in sensitive_terms):
        redacted = "[sensitive-looking content omitted from memory summary]"
    return _truncate(" ".join(redacted.split()), limit)


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _grounding_metadata_from_citations(
    citations: tuple[Any, ...],
    *,
    rag_requested: bool,
    skip_reason: str | None,
) -> dict[str, Any]:
    sources = [
        {
            "path": citation.relative_path,
            "start_line": citation.line_start,
            "end_line": citation.line_end,
            "score": 1.0,
        }
        for citation in citations
    ]
    source_paths = _unique_ordered(citation.relative_path for citation in citations)
    if sources:
        grounding_status = "grounded"
    elif rag_requested and skip_reason == "retrieval_unavailable":
        grounding_status = "weak"
    else:
        grounding_status = "none"
    return {
        "rag_sources": sources,
        "source_count": len(sources),
        "source_paths": source_paths,
        "grounding_status": grounding_status,
    }


def _response_deltas(text: str, *, chunk_size: int = 80) -> list[str]:
    if not text:
        return []
    return [text[index : index + chunk_size] for index in range(0, len(text), chunk_size)]


def _emit_event(
    event_sink: Callable[[dict[str, Any]], None] | None,
    event_type: str,
    data: dict[str, Any],
) -> None:
    if event_sink is None:
        return
    event_sink({"event": event_type, "data": data})


def _rag_trace(reason: str, detail: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = {"count": 0, "reason": reason, **(data or {})}
    return _trace("rag", f"RAG skipped: {reason.replace('_', ' ')}", detail, payload, status="warning")


def _corpus_retrieval_trace(
    retrieval: CorpusRetrievalResult,
) -> dict[str, Any]:
    if retrieval.used:
        return _trace(
            "corpus_rag",
            "Persistent corpus context retrieved",
            f"{len(retrieval.sources)} deduplicated corpus chunk(s) were attached as read-only references.",
            {
                "count": len(retrieval.sources),
                "reason": "corpus_context_relevant",
                "sources": [
                    source.model_dump(mode="json")
                    for source in retrieval.sources
                ],
                "code_executed": False,
            },
        )
    return _trace(
        "corpus_rag",
        "Persistent corpus retrieval not used",
        f"Corpus retrieval continued safely without context: {retrieval.skip_reason}.",
        {
            "count": 0,
            "reason": retrieval.skip_reason,
            "status": retrieval.status,
            "code_executed": False,
        },
        status="warning",
    )


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


def _requested_bullet_count(message: str) -> int | None:
    normalized = "".join(character if character.isdigit() else " " for character in message)
    for token in normalized.split():
        if token.isdigit():
            count = int(token)
            if 1 <= count <= 8:
                return count
    return None


def _context_hint(citations: tuple[Any, ...]) -> str:
    if not citations:
        return ""
    paths = [citation.relative_path for citation in citations[:3]]
    if not paths:
        return ""
    return f"Relevant local context came from: {', '.join(paths)}. "


def _corpus_context_hint(
    retrieval: CorpusRetrievalResult,
) -> str:
    if not retrieval.used:
        return ""
    paths = _unique_ordered(
        source.source_path for source in retrieval.sources
    )[:3]
    if not paths:
        return ""
    return (
        "Persistent corpus references came from: "
        f"{', '.join(paths)}. Their contents were not executed. "
    )


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


def _unique_ordered(values) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return unique
