from __future__ import annotations

from collections import Counter
from typing import Any

from .dataset_registry import DATASET_STATUSES, list_datasets
from .model_audit_logger import load_model_audit_events
from .model_store import MODEL_LIFECYCLE_STATUSES, list_specialist_models
from .trace_store import recent_specialist_traces
from .training_job_store import TRAINING_JOB_STATUSES, list_training_jobs


def build_specialist_dashboard(
    *,
    model_dir: str | None = None,
    dataset_registry_path: str | None = None,
    training_job_store_path: str | None = None,
    audit_path: str | None = None,
    trace_store_path: str | None = None,
    recent_limit: int = 10,
) -> dict[str, Any]:
    models = list_specialist_models(model_dir)["models"]
    datasets = list_datasets(dataset_registry_path)["datasets"]
    jobs = list_training_jobs(training_job_store_path)["jobs"]
    audit = load_model_audit_events(path=audit_path)
    audit_events = audit["events"]
    recent_traces = recent_specialist_traces(limit=recent_limit, path=trace_store_path)

    model_counts = _counts(
        [model.get("lifecycle_status") for model in models if model.get("valid", True)],
        MODEL_LIFECYCLE_STATUSES,
    )
    dataset_counts = _counts(
        [dataset.get("status") for dataset in datasets],
        DATASET_STATUSES,
    )
    job_counts = _counts(
        [job.get("status") for job in jobs],
        TRAINING_JOB_STATUSES,
    )
    promoted_models = [
        model
        for model in models
        if model.get("lifecycle_status") == "promoted" and model.get("valid") is True
    ]

    return {
        "total_models": len([model for model in models if model.get("valid", True)]),
        "models_by_status": model_counts,
        "total_datasets": len(datasets),
        "datasets_by_status": dataset_counts,
        "total_training_jobs": len(jobs),
        "training_jobs_by_status": job_counts,
        "recently_trained_models": _recent_models(models, "created_at", recent_limit),
        "recently_promoted_models": _recent_models(promoted_models, "promoted_at", recent_limit),
        "recent_audit_events": audit_events[-recent_limit:],
        "recent_traces": recent_traces,
        "recent_trace_summary": {
            "total_recent_traces": len(recent_traces),
            "fallback_used_count": len(
                [trace for trace in recent_traces if trace.get("fallback_used") is True]
            ),
            "decision_sources": dict(
                Counter(
                    trace.get("decision_source", "unknown")
                    for trace in recent_traces
                )
            ),
        },
        "fallback_status": {
            "rule_based_fallback_available": True,
            "promoted_model_count": len(promoted_models),
            "fallback_required": len(promoted_models) == 0,
        },
        "read_only": True,
    }


def _counts(values: list[Any], allowed: set[str]) -> dict[str, int]:
    counter = Counter(str(value) for value in values if value in allowed)
    return {status: counter.get(status, 0) for status in sorted(allowed)}


def _recent_models(
    models: list[dict[str, Any]],
    timestamp_field: str,
    limit: int,
) -> list[dict[str, Any]]:
    sortable = [
        model
        for model in models
        if isinstance(model.get("metadata"), dict)
        and model["metadata"].get(timestamp_field)
    ]
    return sorted(
        sortable,
        key=lambda model: model["metadata"].get(timestamp_field, ""),
        reverse=True,
    )[:limit]
