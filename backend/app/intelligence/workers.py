from __future__ import annotations

from typing import Any


def worker_roles() -> list[dict[str, Any]]:
    return [
        _worker("dataset_profiling", "Profile approved local datasets.", [".csv", ".txt", ".tsv"], "intelligence.worker.dataset_profiled"),
        _worker("corpus_indexing", "Index safe project files for RAG.", ["project source files"], "intelligence.worker.corpus_indexed"),
        _worker("model_training", "Train candidate specialist models without promotion.", ["approved training datasets"], "intelligence.worker.model_training"),
        _worker("model_evaluation", "Evaluate candidates and quality gates.", ["candidate model metadata", "evaluation reports"], "intelligence.worker.model_evaluated"),
        _worker("report_package_generation", "Generate report package files only inside approved workspace.", ["report draft", "evidence checklist", "runbook"], "intelligence.worker.report_package_generated"),
        _worker("assignment_analysis", "Run assignment parsing, planning, evidence, and readiness generation.", ["assignment brief", "dataset profile"], "intelligence.worker.assignment_analyzed"),
    ]


def _worker(role_id: str, purpose: str, accepted_inputs: list[str], audit_event_type: str) -> dict[str, Any]:
    return {
        "role_id": role_id,
        "purpose": purpose,
        "accepted_inputs": accepted_inputs,
        "safe_input_paths": "Must be normalized and validated before worker use.",
        "safe_output_paths": "Must remain inside approved workspace or data directories.",
        "status_fields": ["queued", "running", "succeeded", "failed", "cancelled"],
        "logs": "Workers must expose deterministic progress/error messages via job result or trace records.",
        "failure_reasons": ["invalid_input", "unsafe_path", "missing_dependency", "worker_exception", "cancelled"],
        "audit_event_type": audit_event_type,
        "models_may_authorize": False,
    }
