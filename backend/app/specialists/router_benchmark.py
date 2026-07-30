from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from .router_evaluation import load_router_regression_examples
from .schemas import SpecialistRequest
from .specialist_router import route_specialist_task


def benchmark_router(
    examples: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    active_examples = examples if examples is not None else load_router_regression_examples()
    totals_by_task: dict[str, int] = defaultdict(int)
    correct_by_task: dict[str, int] = defaultdict(int)
    confusion: Counter[tuple[str, str]] = Counter()
    failures: list[dict[str, Any]] = []

    for index, example in enumerate(active_examples):
        expected_task = example.get("expected_task_type", "unknown")
        routed = route_specialist_task(
            SpecialistRequest(text=example.get("text", ""), context={"trace_enabled": False})
        )
        actual_task = str(routed.get("task_type", "unknown"))
        totals_by_task[expected_task] += 1
        confusion[(expected_task, actual_task)] += 1
        if actual_task == expected_task and routed.get("recommended_specialist") == example.get(
            "expected_specialist_name"
        ):
            correct_by_task[expected_task] += 1
            continue
        failures.append(
            {
                "index": index,
                "expected_task_type": expected_task,
                "actual_task_type": actual_task,
                "expected_specialist_name": example.get("expected_specialist_name"),
                "actual_specialist_name": routed.get("recommended_specialist"),
            }
        )

    total = len(active_examples)
    correct = sum(correct_by_task.values())
    return {
        "total_examples": total,
        "correct": correct,
        "overall_accuracy": (correct / total) if total else 0.0,
        "accuracy_by_task_type": {
            task: {
                "total_examples": totals_by_task[task],
                "correct": correct_by_task[task],
                "accuracy": (correct_by_task[task] / totals_by_task[task])
                if totals_by_task[task]
                else 0.0,
            }
            for task in sorted(totals_by_task)
        },
        "confusion_counts": {
            f"{expected}->{actual}": count
            for (expected, actual), count in sorted(confusion.items())
        },
        "failures": failures,
        "read_only": True,
        "mutates_traces": False,
    }
