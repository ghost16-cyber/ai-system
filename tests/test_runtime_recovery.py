from __future__ import annotations

from pathlib import Path

from backend.app.local_ai.contracts import (
    AdmissionOutcome,
    HardwareAdmissionDecision,
)
from backend.app.runtime.caches import CacheRegistry
from backend.app.runtime.contracts import RuntimeState
from backend.app.runtime.recovery import RecoveryCoordinator
from tests.test_runtime_manager import _runtime


def test_provider_admission_blocked_is_reported_and_stays_pending(
    tmp_path: Path, monkeypatch,
) -> None:
    """Category: provider recovery / GPU unavailable / GPU OOM. The provider
    adapter must route through the existing admission_preview -- never
    reimplement VRAM math -- and a blocked outcome must surface as a
    genuinely pending recovery (it cannot self-heal by merely re-checking)."""
    manager, persistence, _database = _runtime(tmp_path)
    manager.initialize()

    def _blocked_admission_preview(request, *, report=None):
        return HardwareAdmissionDecision(
            outcome=AdmissionOutcome.BLOCKED_VRAM,
            reason="Insufficient VRAM headroom.",
            estimated_required_bytes=2_000_000_000,
            safety_reserve_bytes=500_000_000,
        )

    monkeypatch.setattr(
        manager._providers._local_ai_service, "admission_preview", _blocked_admission_preview
    )
    coordinator = RecoveryCoordinator(persistence)
    manager._recovery_coordinator = coordinator

    readiness = manager.recover()
    assert readiness.pending_recovery is True
    assert "recovery_pending" in readiness.blocking_reasons
    assert readiness.state == RuntimeState.DEGRADED
    assert manager.state == RuntimeState.DEGRADED

    events = persistence.recent_recovery_events()
    assert any(row["failure_class"] == "provider_admission_blocked" for row in events)


def test_expired_lease_and_job_recovery_is_self_healing_and_not_pending(
    tmp_path: Path,
) -> None:
    """Category: interrupted indexing / restart after crash. Expired-lease
    recovery (coordinator leases, local_ai jobs) is resolved by the recover()
    call itself, so it must not block readiness afterward."""
    manager, persistence, _database = _runtime(tmp_path)
    manager.initialize()
    coordinator = RecoveryCoordinator(persistence)
    manager._recovery_coordinator = coordinator

    readiness = manager.recover()
    assert readiness.pending_recovery is False
    assert readiness.ready is True
    assert manager.state == RuntimeState.READY


def test_recover_is_a_safe_no_op_outside_ready_or_degraded(tmp_path: Path) -> None:
    manager, persistence, _database = _runtime(tmp_path)
    coordinator = RecoveryCoordinator(persistence)
    manager._recovery_coordinator = coordinator
    # Never initialized: state is STOPPED.
    readiness = manager.recover()
    assert manager.state == RuntimeState.STOPPED
    assert readiness.ready is False


def test_restart_after_crash_reaches_ready_again_from_the_same_database(
    tmp_path: Path,
) -> None:
    """Category: restart after crash / partial runtime startup. A second
    RuntimeManager instance built against the same already-migrated database
    must reach READY on its own -- current state is never trusted from a
    prior process, only recomputed."""
    first_manager, _persistence, database = _runtime(tmp_path, name="restart.db")
    first_readiness = first_manager.initialize()
    assert first_readiness.ready is True
    first_manager.shutdown()

    second_manager, _persistence2, _database2 = _runtime(tmp_path, name="restart.db")
    second_readiness = second_manager.initialize()
    assert second_readiness.ready is True
    assert second_manager.state == RuntimeState.READY


def test_cache_corruption_recovery_clears_and_forces_deterministic_recompute() -> None:
    """Category: cache corruption. Clearing a cache is the deterministic
    recovery action -- it never touches the durable stores the cache
    accelerates access to, only forces the next lookups to miss."""
    registry = CacheRegistry()
    registry.embedding.set("k", "possibly-corrupted-value")
    assert registry.embedding.size == 1

    registry.clear_all()

    assert registry.embedding.size == 0
    assert registry.embedding.get("k") is None
    assert registry.embedding.misses == 1
