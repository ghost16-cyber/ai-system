from __future__ import annotations

from collections import Counter
from typing import Any

from backend.app.jobs.queue import JobQueue
from backend.app.schemas.api import ChatRunResponse
from backend.app.specialists.model_audit_logger import load_model_audit_events
from backend.app.specialists.model_store import list_specialist_models
from backend.app.specialists.trace_store import recent_specialist_traces

from .policy import model_use_policy
from .registry import intelligence_components
from .workers import worker_roles


def build_intelligence_dashboard(
    *,
    chat_runs: list[ChatRunResponse],
    job_queue: JobQueue,
    model_dir: str | None = None,
    audit_path: str | None = None,
    trace_store_path: str | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    models = list_specialist_models(model_dir)["models"]
    audit_events = load_model_audit_events(path=audit_path)["events"]
    specialist_traces = recent_specialist_traces(limit=limit, path=trace_store_path)
    jobs = job_queue.list_jobs(limit=limit)
    decision_traces = decision_traces_from_chat_runs(chat_runs[:limit])
    return {
        "components": intelligence_components(),
        "policy": model_use_policy(),
        "worker_roles": worker_roles(),
        "worker_status": {
            "recent_jobs": [
                {
                    "job_id": job.job_id,
                    "job_type": job.job_type,
                    "status": job.status,
                    "error": job.error,
                    "created_at": job.created_at.isoformat(),
                    "started_at": job.started_at.isoformat() if job.started_at else None,
                    "finished_at": job.finished_at.isoformat() if job.finished_at else None,
                    "audit_event_type": "intelligence.worker.job_event",
                }
                for job in jobs
            ],
            "job_counts": dict(Counter(job.status for job in jobs)),
        },
        "model_evaluation_summary": {
            "models": [_model_summary(model, audit_events, specialist_traces) for model in models],
            "counts_by_status": dict(Counter(str(model.get("lifecycle_status", "invalid")) for model in models)),
            "fallback_count": sum(1 for trace in specialist_traces if trace.get("fallback_used") is True),
            "low_confidence_count": sum(1 for trace in specialist_traces if _low_confidence(trace.get("confidence"))),
            "recent_audit_events": audit_events[-limit:],
        },
        "decision_traces": decision_traces,
        "auditability": {
            "models_authorize_safety": False,
            "path_safety_authority": "deterministic_safety_gates",
            "file_write_authority": "deterministic_safety_gates",
            "command_execution_authority": "deterministic_safety_gates",
        },
    }


def decision_traces_from_chat_runs(runs: list[ChatRunResponse]) -> list[dict[str, Any]]:
    traces = []
    for run in runs:
        checks = [
            entry.get("title", entry.get("phase", "check"))
            for entry in run.trace_summary
            if entry.get("phase") in {"runtime", "safety", "rag", "specialist", "slm", "accepted", "memory"}
        ]
        traces.append(
            {
                "trace_id": f"decision:{run.run_id}",
                "run_id": run.run_id,
                "created_at": run.created_at.isoformat(),
                "user_request": run.user_message,
                "selected_specialist": run.selected_specialist,
                "rag": {
                    "used": run.rag_used,
                    "skip_reason": run.rag_skip_reason,
                    "source_count": run.source_count,
                    "grounding_status": run.grounding_status,
                },
                "slm": {
                    "used": run.used_real_slm,
                    "provider": run.slm_provider,
                    "model": run.slm_model,
                    "fallback_reason": run.slm_fallback_reason,
                },
                "deterministic_checks_applied": checks,
                "worker_used": None,
                "final_safety_status": run.safety_decision,
                "reason_for_final_output": _reason(run),
            }
        )
    return traces


def _model_summary(model: dict[str, Any], audit_events: list[dict[str, Any]], traces: list[dict[str, Any]]) -> dict[str, Any]:
    metadata = model.get("metadata") if isinstance(model.get("metadata"), dict) else {}
    model_id = str(model.get("model_id") or metadata.get("model_id") or "")
    related_events = [event for event in audit_events if event.get("model_id") == model_id]
    related_traces = [
        trace for trace in traces
        if trace.get("model_id") == model_id or trace.get("specialist_name") == metadata.get("specialist")
    ]
    return {
        "model_id": model_id,
        "specialist": metadata.get("specialist") or model.get("specialist"),
        "used_for": "advisory task classification and specialist prediction",
        "status": model.get("lifecycle_status") or metadata.get("lifecycle_status") or "invalid",
        "active": model.get("active") is True,
        "latest_metrics": metadata.get("metrics", {}),
        "quality_gate": metadata.get("quality_gate", {}),
        "fallback_count": sum(1 for trace in related_traces if trace.get("fallback_used") is True),
        "low_confidence_count": sum(1 for trace in related_traces if _low_confidence(trace.get("confidence"))),
        "recent_audit_events": related_events[-5:],
    }


def _low_confidence(value: Any) -> bool:
    return isinstance(value, (int, float)) and float(value) < 0.6


def _reason(run: ChatRunResponse) -> str:
    if run.safety_decision == "block":
        return "Deterministic safety policy blocked the requested plan."
    if run.rag_used and run.grounding_status == "grounded":
        return "Response used project RAG context and deterministic safety checks."
    if run.used_real_slm:
        return "SLM response was advisory and combined with deterministic safety checks."
    return "Deterministic fallback guidance was used with safety checks."
