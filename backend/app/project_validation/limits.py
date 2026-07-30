from __future__ import annotations

from dataclasses import dataclass

from backend.app.project_validation.contracts import BudgetUsage, ValidationLimits


class BudgetExceededError(RuntimeError):
    def __init__(self, limit_name: str, used: int, maximum: int):
        super().__init__(f"Validation limit exceeded: {limit_name} ({used}/{maximum}).")
        self.limit_name = limit_name
        self.used = used
        self.maximum = maximum


@dataclass(frozen=True)
class BudgetStatus:
    warnings: tuple[str, ...]
    exceeded: tuple[str, ...]
    remaining: dict[str, int]


_LIMIT_TO_USAGE = {
    "max_duration_seconds": "duration_seconds",
    "max_command_executions": "command_executions",
    "max_command_runtime_seconds": "command_runtime_seconds",
    "max_repair_attempts": "repair_attempts",
    "max_plan_revisions": "plan_revisions",
    "max_work_unit_retries": "work_unit_retries",
    "max_model_calls": "model_calls",
    "max_model_input_chars": "model_input_chars",
    "max_model_output_chars": "model_output_chars",
    "max_evidence_items": "evidence_items",
    "max_generated_files": "generated_files",
    "max_modified_files": "modified_files",
    "max_deleted_files": "deleted_files",
    "max_total_changed_bytes": "total_changed_bytes",
    "max_test_reruns": "test_reruns",
    "max_snapshot_files": "snapshot_files",
    "max_snapshot_bytes": "snapshot_bytes",
}


def evaluate_budget(limits: ValidationLimits, usage: BudgetUsage, *, warning_ratio: float = 0.8) -> BudgetStatus:
    warnings: list[str] = []
    exceeded: list[str] = []
    remaining: dict[str, int] = {}
    for limit_name, usage_name in _LIMIT_TO_USAGE.items():
        maximum = int(getattr(limits, limit_name))
        used = int(getattr(usage, usage_name))
        remaining[usage_name] = max(0, maximum - used)
        if used > maximum:
            exceeded.append(usage_name)
        elif maximum > 0 and used / maximum >= warning_ratio:
            warnings.append(usage_name)
    return BudgetStatus(tuple(warnings), tuple(exceeded), remaining)


def add_usage(usage: BudgetUsage, **changes: int) -> BudgetUsage:
    payload = usage.model_dump()
    for name, amount in changes.items():
        if name not in payload:
            raise ValueError(f"Unknown budget usage field: {name}")
        if amount < 0:
            raise ValueError("Budget usage increments must be non-negative.")
        payload[name] = int(payload[name]) + int(amount)
    return BudgetUsage.model_validate(payload)


def enforce_budget(limits: ValidationLimits, usage: BudgetUsage) -> None:
    status = evaluate_budget(limits, usage)
    if status.exceeded:
        name = status.exceeded[0]
        limit_name = next(key for key, value in _LIMIT_TO_USAGE.items() if value == name)
        raise BudgetExceededError(name, int(getattr(usage, name)), int(getattr(limits, limit_name)))


__all__ = ["BudgetExceededError", "BudgetStatus", "add_usage", "enforce_budget", "evaluate_budget"]
