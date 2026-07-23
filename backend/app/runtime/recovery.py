from __future__ import annotations

from typing import Iterable

from backend.app.runtime.contracts import RecoveryEvent
from backend.app.runtime.persistence import RuntimePersistence

# Failure classes whose adapter.recover() call itself resolves the
# condition (expired lease/job recovery is unconditionally fixed by the
# recovery call). Anything else (e.g. "provider_admission_blocked") means
# recover() only detected and reported an ongoing condition -- it stays
# "pending" until a later recovery pass observes it resolved.
_SELF_HEALING_FAILURE_CLASSES = frozenset({
    "expired_lease_or_job",
    "expired_local_ai_job",
    "expired_coordinator_lease",
})


class RecoveryCoordinator:
    """Runs deterministic recovery across every registered subsystem by
    calling each one's own existing `recover()` (see adapters.py) -- this
    coordinator holds no subsystem-specific recovery logic itself and never
    reprocesses a completed action (every underlying recovery method is
    already idempotent: `recover_expired_jobs`/`recover_expired_leases`
    requeue by lease-expiry only, and `admission_preview` is a pure read).
    """

    def __init__(self, persistence: RuntimePersistence | None = None) -> None:
        self._persistence = persistence
        self._last_events: tuple[RecoveryEvent, ...] = ()

    def run_recovery(self, subsystems: Iterable[object]) -> tuple[RecoveryEvent, ...]:
        events: list[RecoveryEvent] = []
        for subsystem in subsystems:
            recover_method = getattr(subsystem, "recover", None)
            if not callable(recover_method):
                continue
            event = recover_method()
            if event is None:
                continue
            events.append(event)
            if self._persistence is not None:
                self._persistence.record_recovery_event(
                    recovery_id=event.recovery_id,
                    failure_class=event.failure_class,
                    subsystem_id=event.subsystem_id,
                    action=event.action,
                    outcome=event.outcome,
                    detail=event.model_dump(mode="json"),
                )
        self._last_events = tuple(events)
        return self._last_events

    def pending(self) -> tuple[RecoveryEvent, ...]:
        return tuple(
            event for event in self._last_events
            if event.failure_class not in _SELF_HEALING_FAILURE_CLASSES
        )
