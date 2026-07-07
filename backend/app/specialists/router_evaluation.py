from __future__ import annotations

from typing import Any

from .schemas import SpecialistRequest
from .specialist_router import route_specialist_task


ROUTER_REGRESSION_EXAMPLES: list[dict[str, str]] = [
    {
        "text": "CUDA runtime reports out of memory on the local GPU",
        "expected_task_type": "runtime",
        "expected_specialist_name": "runtime_specialist",
    },
    {
        "text": "Security token and credential policy review is needed",
        "expected_task_type": "safety",
        "expected_specialist_name": "safety_specialist",
    },
    {
        "text": "Pytest failure traceback points to a parser bug",
        "expected_task_type": "bug_triage",
        "expected_specialist_name": "bug_triage_specialist",
    },
    {
        "text": "Refactor this module for typing and code quality",
        "expected_task_type": "code_quality",
        "expected_specialist_name": "code_quality_specialist",
    },
    {
        "text": "Build a RAG retrieval index with embeddings and FAISS",
        "expected_task_type": "rag",
        "expected_specialist_name": "rag_specialist",
    },
    {
        "text": "Tune PyTorch training batch size across epochs",
        "expected_task_type": "training",
        "expected_specialist_name": "training_specialist",
    },
    {
        "text": "Give a general implementation recommendation",
        "expected_task_type": "general",
        "expected_specialist_name": "general_specialist",
    },
]


def load_router_regression_examples() -> list[dict[str, str]]:
    return [dict(example) for example in ROUTER_REGRESSION_EXAMPLES]


def evaluate_router_regression(
    examples: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    active_examples = examples if examples is not None else load_router_regression_examples()
    failures: list[dict[str, Any]] = []
    correct = 0

    for index, example in enumerate(active_examples):
        routed = route_specialist_task(
            SpecialistRequest(text=example.get("text", ""), context={"trace_enabled": False})
        )
        passed = (
            routed.get("task_type") == example.get("expected_task_type")
            and routed.get("recommended_specialist") == example.get("expected_specialist_name")
        )
        if passed:
            correct += 1
            continue
        failures.append(
            {
                "index": index,
                "text": example.get("text", ""),
                "expected_task_type": example.get("expected_task_type"),
                "expected_specialist_name": example.get("expected_specialist_name"),
                "actual_task_type": routed.get("task_type"),
                "actual_specialist_name": routed.get("recommended_specialist"),
            }
        )

    total = len(active_examples)
    return {
        "total_examples": total,
        "correct": correct,
        "accuracy": (correct / total) if total else 0.0,
        "failures": failures,
        "read_only": True,
        "mutates_traces": False,
    }
