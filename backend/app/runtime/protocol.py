from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from backend.app.runtime.contracts import RecoveryEvent, RuntimeSubsystemHealth


@runtime_checkable
class RuntimeSubsystem(Protocol):
    """The uniform lifecycle contract every RuntimeManager-registered subsystem
    exposes. Implementations are thin adapters (see adapters.py) around the
    existing authoritative singletons -- they never reimplement retrieval,
    execution, mutation, or approval logic themselves.
    """

    subsystem_id: str

    def initialize(self) -> None: ...

    def shutdown(self) -> None: ...

    def health(self) -> RuntimeSubsystemHealth: ...

    def ready(self) -> bool: ...

    def recover(self) -> RecoveryEvent | None: ...


@dataclass(frozen=True, slots=True)
class SubsystemRegistration:
    subsystem: RuntimeSubsystem
    init_order: int
    required_for_ready: bool = True
