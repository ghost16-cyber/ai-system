from __future__ import annotations

from backend.app.project_validation.contracts import ValidationState


_ALLOWED: dict[ValidationState, set[ValidationState]] = {
    ValidationState.CREATED: {ValidationState.PREPARING_WORKSPACE, ValidationState.CANCELLED, ValidationState.FAILED},
    ValidationState.PREPARING_WORKSPACE: {ValidationState.BASELINE_CAPTURED, ValidationState.FAILED, ValidationState.RECOVERING},
    ValidationState.BASELINE_CAPTURED: {ValidationState.READY, ValidationState.FAILED},
    ValidationState.READY: {ValidationState.RUNNING, ValidationState.CANCELLED, ValidationState.FAILED},
    ValidationState.RUNNING: {
        ValidationState.EVALUATING_ACCEPTANCE, ValidationState.EXECUTION_PAUSED,
        ValidationState.BUDGET_EXCEEDED, ValidationState.RECOVERING,
        ValidationState.CANCELLED, ValidationState.FAILED,
    },
    ValidationState.EXECUTION_PAUSED: {ValidationState.RUNNING, ValidationState.RECOVERING, ValidationState.CANCELLED, ValidationState.FAILED},
    ValidationState.BUDGET_EXCEEDED: {ValidationState.EXECUTION_PAUSED, ValidationState.CANCELLED, ValidationState.FAILED},
    ValidationState.EVALUATING_ACCEPTANCE: {ValidationState.INSPECTING_DELIVERABLES, ValidationState.REMEDIATION_REQUIRED, ValidationState.RECOVERING, ValidationState.CANCELLED, ValidationState.FAILED},
    ValidationState.INSPECTING_DELIVERABLES: {ValidationState.RUNNING_REGRESSION, ValidationState.REMEDIATION_REQUIRED, ValidationState.RECOVERING, ValidationState.CANCELLED, ValidationState.FAILED},
    ValidationState.RUNNING_REGRESSION: {ValidationState.QUALITY_REVIEW, ValidationState.REMEDIATION_REQUIRED, ValidationState.RECOVERING, ValidationState.CANCELLED, ValidationState.FAILED},
    ValidationState.QUALITY_REVIEW: {ValidationState.AWAITING_HUMAN_REVIEW, ValidationState.REMEDIATION_REQUIRED, ValidationState.RECOVERING, ValidationState.CANCELLED, ValidationState.FAILED},
    ValidationState.REMEDIATION_REQUIRED: {ValidationState.READY, ValidationState.CANCELLED, ValidationState.FAILED},
    ValidationState.AWAITING_HUMAN_REVIEW: {
        ValidationState.DELIVERY_READY, ValidationState.DELIVERY_REJECTED,
        ValidationState.REMEDIATION_REQUIRED, ValidationState.CANCELLED,
    },
    ValidationState.RECOVERING: {ValidationState.EXECUTION_PAUSED, ValidationState.READY, ValidationState.CANCELLED, ValidationState.FAILED},
    ValidationState.DELIVERY_READY: set(),
    ValidationState.DELIVERY_REJECTED: {ValidationState.REMEDIATION_REQUIRED, ValidationState.CANCELLED},
    ValidationState.CANCELLED: set(),
    ValidationState.FAILED: {ValidationState.RECOVERING, ValidationState.CANCELLED},
}


def transition_state(current: ValidationState | str, target: ValidationState | str) -> ValidationState:
    current_state = ValidationState(current)
    target_state = ValidationState(target)
    if target_state not in _ALLOWED[current_state]:
        raise ValueError(f"Invalid validation transition: {current_state.value} -> {target_state.value}")
    return target_state


__all__ = ["transition_state"]
