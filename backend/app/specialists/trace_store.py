from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4


DEFAULT_SPECIALIST_TRACE_PATH = Path("data/specialists/specialist_traces.jsonl")
INPUT_PREVIEW_LIMIT = 80


def append_specialist_trace(
    *,
    request_type: str,
    task_type: str | None = None,
    recommended_specialist: str | None = None,
    specialist_name: str | None = None,
    confidence: float | None = None,
    model_id: str | None = None,
    promoted_model_available: bool = False,
    fallback_required: bool = False,
    fallback_used: bool | None = None,
    decision_source: str = "rule_fallback",
    safety_notes: list[str] | None = None,
    input_text: str | None = None,
    extra: dict[str, Any] | None = None,
    path: str | Path | None = None,
) -> dict[str, Any]:
    trace_path = Path(path or DEFAULT_SPECIALIST_TRACE_PATH)
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "trace_id": f"specialist-trace-{uuid4().hex[:12]}",
        "timestamp": _utc_now(),
        "request_type": request_type,
        "task_type": task_type,
        "recommended_specialist": recommended_specialist,
        "specialist_name": specialist_name,
        "confidence": confidence,
        "model_id": model_id,
        "promoted_model_available": promoted_model_available,
        "fallback_required": fallback_required,
        "fallback_used": fallback_used,
        "decision_source": decision_source,
        "safety_notes": safety_notes or [],
        **_safe_input_fields(input_text),
        **(extra or {}),
    }
    with trace_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")
    return {"stored": True, "path": str(trace_path), "trace": record}


def list_specialist_traces(
    *,
    specialist_name: str | None = None,
    task_type: str | None = None,
    fallback_used: bool | None = None,
    decision_source: str | None = None,
    path: str | Path | None = None,
) -> dict[str, Any]:
    trace_path = Path(path or DEFAULT_SPECIALIST_TRACE_PATH)
    traces, errors, missing = _load_traces(trace_path)
    filtered = [
        trace
        for trace in traces
        if _matches_filters(
            trace,
            specialist_name=specialist_name,
            task_type=task_type,
            fallback_used=fallback_used,
            decision_source=decision_source,
        )
    ]
    return {
        "path": str(trace_path),
        "traces": filtered,
        "count": len(filtered),
        "total_traces": len(traces),
        "filters": {
            "specialist_name": specialist_name,
            "task_type": task_type,
            "fallback_used": fallback_used,
            "decision_source": decision_source,
        },
        "errors": errors,
        "missing": missing,
    }


def get_specialist_trace(
    trace_id: str,
    path: str | Path | None = None,
) -> dict[str, Any] | None:
    trace_path = Path(path or DEFAULT_SPECIALIST_TRACE_PATH)
    traces, _, _ = _load_traces(trace_path)
    for trace in traces:
        if trace.get("trace_id") == trace_id:
            return trace
    return None


def recent_specialist_traces(
    *,
    limit: int = 10,
    path: str | Path | None = None,
) -> list[dict[str, Any]]:
    traces = list_specialist_traces(path=path)["traces"]
    return sorted(traces, key=lambda trace: trace.get("timestamp", ""), reverse=True)[:limit]


def _matches_filters(
    trace: dict[str, Any],
    *,
    specialist_name: str | None,
    task_type: str | None,
    fallback_used: bool | None,
    decision_source: str | None,
) -> bool:
    if specialist_name and specialist_name not in {
        trace.get("recommended_specialist"),
        trace.get("specialist_name"),
    }:
        return False
    if task_type and trace.get("task_type") != task_type:
        return False
    if fallback_used is not None and trace.get("fallback_used") is not fallback_used:
        return False
    if decision_source and trace.get("decision_source") != decision_source:
        return False
    return True


def _load_traces(trace_path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    traces: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    if not trace_path.exists():
        return traces, errors, True

    with trace_path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                trace = json.loads(stripped)
            except json.JSONDecodeError as error:
                errors.append({"line": line_number, "error": error.msg})
                continue
            if isinstance(trace, dict):
                traces.append(trace)
    return traces, errors, False


def _safe_input_fields(input_text: str | None) -> dict[str, Any]:
    if input_text is None:
        return {"input_preview": None, "input_hash": None, "input_truncated": False}
    preview = input_text[:INPUT_PREVIEW_LIMIT]
    return {
        "input_preview": preview,
        "input_hash": hashlib.sha256(input_text.encode("utf-8")).hexdigest(),
        "input_truncated": len(input_text) > INPUT_PREVIEW_LIMIT,
    }


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
