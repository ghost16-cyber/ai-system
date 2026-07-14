from __future__ import annotations

from backend.app.client_engagement.contracts import EngagementState


class EngagementTransitionError(ValueError):
    pass


TRANSITIONS: dict[EngagementState, frozenset[EngagementState]] = {
    EngagementState.DRAFT: frozenset({EngagementState.COLLECTING_EVIDENCE, EngagementState.CANCELLED, EngagementState.FAILED}),
    EngagementState.COLLECTING_EVIDENCE: frozenset({EngagementState.EXTRACTING_REQUIREMENTS, EngagementState.FAILED, EngagementState.CANCELLED}),
    EngagementState.EXTRACTING_REQUIREMENTS: frozenset({EngagementState.CLARIFICATION_REQUIRED, EngagementState.SCOPE_PREPARING, EngagementState.FAILED, EngagementState.CANCELLED}),
    EngagementState.CLARIFICATION_REQUIRED: frozenset({EngagementState.CLARIFICATION_REQUIRED, EngagementState.SCOPE_PREPARING, EngagementState.CANCELLED, EngagementState.FAILED}),
    EngagementState.SCOPE_PREPARING: frozenset({EngagementState.SCOPE_READY, EngagementState.CLARIFICATION_REQUIRED, EngagementState.FAILED, EngagementState.CANCELLED}),
    EngagementState.SCOPE_READY: frozenset({EngagementState.AWAITING_SCOPE_APPROVAL, EngagementState.SCOPE_PREPARING, EngagementState.CANCELLED, EngagementState.FAILED}),
    EngagementState.AWAITING_SCOPE_APPROVAL: frozenset({EngagementState.SCOPE_APPROVED, EngagementState.SCOPE_PREPARING, EngagementState.CANCELLED, EngagementState.FAILED}),
    EngagementState.SCOPE_APPROVED: frozenset({EngagementState.PROJECT_LAUNCHING, EngagementState.SCOPE_CHANGE_REQUESTED, EngagementState.CANCELLED, EngagementState.FAILED}),
    EngagementState.PROJECT_LAUNCHING: frozenset({EngagementState.PROJECT_LAUNCHED, EngagementState.SCOPE_APPROVED, EngagementState.FAILED}),
    EngagementState.PROJECT_LAUNCHED: frozenset({EngagementState.SCOPE_CHANGE_REQUESTED, EngagementState.CANCELLED, EngagementState.FAILED}),
    EngagementState.SCOPE_CHANGE_REQUESTED: frozenset({EngagementState.SCOPE_CHANGE_REVIEW, EngagementState.CANCELLED, EngagementState.FAILED}),
    EngagementState.SCOPE_CHANGE_REVIEW: frozenset({EngagementState.SCOPE_APPROVED, EngagementState.AWAITING_SCOPE_APPROVAL, EngagementState.CANCELLED, EngagementState.FAILED}),
    EngagementState.CANCELLED: frozenset(),
    EngagementState.FAILED: frozenset({EngagementState.COLLECTING_EVIDENCE, EngagementState.SCOPE_APPROVED, EngagementState.CANCELLED}),
}


def transition_state(current: EngagementState | str, target: EngagementState | str) -> EngagementState:
    source = EngagementState(current)
    destination = EngagementState(target)
    if destination not in TRANSITIONS[source]:
        raise EngagementTransitionError(f"Invalid engagement transition: {source.value} -> {destination.value}")
    return destination


__all__ = ["EngagementTransitionError", "TRANSITIONS", "transition_state"]
