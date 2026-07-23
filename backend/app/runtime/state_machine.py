from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from backend.app.runtime.contracts import RuntimeState, RuntimeStateTransition
from backend.app.runtime.persistence import RuntimePersistence


class RuntimeStateError(RuntimeError):
    """Raised when an illegal runtime state transition is attempted."""


_ALLOWED: frozenset[tuple[RuntimeState, RuntimeState]] = frozenset({
    (RuntimeState.STOPPED, RuntimeState.INITIALIZING),
    (RuntimeState.INITIALIZING, RuntimeState.READY),
    (RuntimeState.INITIALIZING, RuntimeState.DEGRADED),
    (RuntimeState.READY, RuntimeState.DEGRADED),
    (RuntimeState.DEGRADED, RuntimeState.READY),
    (RuntimeState.DEGRADED, RuntimeState.RECOVERING),
    (RuntimeState.READY, RuntimeState.RECOVERING),
    (RuntimeState.RECOVERING, RuntimeState.READY),
    (RuntimeState.RECOVERING, RuntimeState.DEGRADED),
    (RuntimeState.READY, RuntimeState.STOPPING),
    (RuntimeState.DEGRADED, RuntimeState.STOPPING),
    (RuntimeState.RECOVERING, RuntimeState.STOPPING),
    (RuntimeState.STOPPING, RuntimeState.STOPPED),
})


class RuntimeStateMachine:
    """Enforces "explicit transitions, no hidden state": the current state
    only ever changes through `transition()`, which validates against a
    frozen table and rejects anything not in it. Every accepted transition is
    appended to an in-memory ring buffer (for `GET /runtime`) and, if a
    persistence DAO is supplied, to the durable `runtime_state_events` table.
    Current state itself lives in memory only; the transition *audit* is
    durable, so a process restart always re-enters at STOPPED->INITIALIZING
    and recomputes readiness rather than trusting a stale persisted "READY".
    """

    def __init__(
        self,
        persistence: RuntimePersistence | None = None,
        *,
        ring_buffer_size: int = 50,
    ) -> None:
        self._state = RuntimeState.STOPPED
        self._persistence = persistence
        self._transitions: deque[RuntimeStateTransition] = deque(maxlen=ring_buffer_size)

    @property
    def state(self) -> RuntimeState:
        return self._state

    def transition(
        self,
        to_state: RuntimeState,
        *,
        trigger: str,
        reason: str | None = None,
        detail: dict[str, Any] | None = None,
        persist: bool = True,
    ) -> RuntimeStateTransition:
        """`persist=False` is for the one transition that can legitimately
        precede durable storage existing at all: the initial
        STOPPED -> INITIALIZING transition, issued before any subsystem
        (including whichever one bootstraps the schema) has run
        `initialize()`. Every other transition happens after the schema is
        guaranteed to exist and is always persisted.
        """
        from_state = self._state
        if (from_state, to_state) not in _ALLOWED:
            raise RuntimeStateError(
                f"Illegal runtime state transition: {from_state.value} -> {to_state.value}"
            )
        record = RuntimeStateTransition(
            from_state=from_state,
            to_state=to_state,
            trigger=trigger,
            reason=reason,
            occurred_at=datetime.now(timezone.utc),
        )
        self._state = to_state
        self._transitions.append(record)
        if persist and self._persistence is not None:
            self._persistence.record_state_event(
                event_id=str(uuid4()),
                from_state=from_state.value,
                to_state=to_state.value,
                trigger=trigger,
                reason=reason,
                detail=detail or {},
            )
        return record

    def recent_transitions(self) -> tuple[RuntimeStateTransition, ...]:
        return tuple(self._transitions)
