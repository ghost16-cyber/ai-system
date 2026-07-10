from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.app.schemas.api import ChatRunResponse
from backend.app.training_data.label_policy import suggest_label
from backend.app.training_data.schemas import (
    TrainingExample,
    TrainingExampleCreateRequest,
)
from backend.app.training_data.storage import append_example

MAX_MESSAGE_CHARS = 4000
MAX_ASSISTANT_RESPONSE_CHARS = 1600

REDACTION_PATTERNS = (
    re.compile(
        r"(?i)\b(api[_-]?key|access[_-]?token|secret|password|passwd|pwd|token)\s*[:=]\s*([^\s'\";,]+)"
    ),
    re.compile(r"(?i)\b(bearer)\s+[a-z0-9._~+/=-]{12,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"(?m)^\s*[A-Z0-9_]*(SECRET|TOKEN|PASSWORD|API_KEY)[A-Z0-9_]*\s*=.*$"),
)


def log_chat_run_example(
    workspace_root: str | Path,
    run: ChatRunResponse,
) -> dict[str, Any]:
    suggested_label = suggest_label(
        run.user_message,
        routed_task_type=run.intent,
        routed_specialist=run.selected_specialist,
        rag_used=run.rag_used,
        source_paths=run.source_paths,
    )
    example = TrainingExample(
        id=f"intent-example-{uuid4().hex[:12]}",
        created_at=datetime.now(UTC),
        source="chat_run",
        chat_run_id=run.run_id,
        user_message=redact_text(run.user_message, max_chars=MAX_MESSAGE_CHARS),
        assistant_response=redact_text(
            run.assistant_response,
            max_chars=MAX_ASSISTANT_RESPONSE_CHARS,
        ),
        routed_task_type=run.intent,
        routed_specialist=run.selected_specialist,
        routing_confidence=run.confidence,
        rag_used=run.rag_used,
        rag_skip_reason=run.rag_skip_reason,
        grounding_status=run.grounding_status,
        source_paths=list(run.source_paths),
        safety_status=run.safety_decision,
        suggested_label=suggested_label,
        label_status="suggested",
    )
    return append_example(workspace_root, example)


def log_manual_example(
    workspace_root: str | Path,
    request: TrainingExampleCreateRequest,
) -> dict[str, Any]:
    suggested = request.suggested_label or suggest_label(
        request.user_message,
        routed_task_type=request.routed_task_type,
        routed_specialist=request.routed_specialist,
        rag_used=request.rag_used,
        source_paths=request.source_paths,
    )
    final_label = request.final_label
    label_status = request.label_status or (
        "confirmed" if final_label else "suggested" if suggested else "unlabeled"
    )
    example = TrainingExample(
        id=f"intent-example-{uuid4().hex[:12]}",
        created_at=datetime.now(UTC),
        source=request.source,
        user_message=redact_text(request.user_message, max_chars=MAX_MESSAGE_CHARS),
        assistant_response=(
            redact_text(request.assistant_response, max_chars=MAX_ASSISTANT_RESPONSE_CHARS)
            if request.assistant_response
            else None
        ),
        routed_task_type=request.routed_task_type,
        routed_specialist=request.routed_specialist,
        routing_confidence=request.routing_confidence,
        rag_used=request.rag_used,
        rag_skip_reason=request.rag_skip_reason,
        grounding_status=request.grounding_status,
        source_paths=request.source_paths,
        safety_status=request.safety_status,
        suggested_label=suggested,
        corrected_label=request.corrected_label,
        final_label=final_label,
        label_status=label_status,
        usefulness_rating=request.usefulness_rating,
        notes=redact_text(request.notes, max_chars=500) if request.notes else None,
    )
    return append_example(workspace_root, example, dedupe_chat_run_id=False)


def redact_text(text: str | None, *, max_chars: int) -> str:
    if not text:
        return ""
    redacted = text
    for pattern in REDACTION_PATTERNS:
        redacted = pattern.sub(_replacement, redacted)
    return redacted[:max_chars]


def _replacement(match: re.Match[str]) -> str:
    if match.lastindex and match.lastindex >= 1:
        prefix = match.group(1)
        if prefix.lower() == "bearer":
            return "Bearer [REDACTED]"
        return f"{prefix}=[REDACTED]"
    return "[REDACTED]"
