from __future__ import annotations

from datetime import datetime, timezone

from backend.app.local_ai.contracts import CapabilityStatus
from backend.app.runtime.adapters import (
    LocalAIAdapter,
    ProviderAdapter,
    RetrievalAdapter,
)
from backend.app.runtime.adapters import ProjectControlAdapter
from backend.app.runtime.contracts import (
    RuntimeCacheStatus,
    RuntimeCorpusStatus,
    RuntimeHealthReport,
    RuntimeJobsStatus,
    RuntimeReadiness,
    RuntimeSnapshot,
    RuntimeState,
    RuntimeTelemetrySnapshot,
)
from backend.app.runtime.persistence import RuntimePersistence
from backend.app.runtime.protocol import SubsystemRegistration
from backend.app.runtime.state_machine import RuntimeStateMachine


def _now() -> datetime:
    return datetime.now(timezone.utc)


class RuntimeManager:
    """The sole lifecycle authority for Astra's runtime. It owns startup,
    shutdown, readiness, and health aggregation over the existing
    subsystems -- it never gains retrieval, execution, mutation, or
    approval authority itself; every check here is a read of an existing
    subsystem's own public state (see adapters.py).
    """

    def __init__(
        self,
        registrations: tuple[SubsystemRegistration, ...],
        *,
        state_machine: RuntimeStateMachine,
        persistence: RuntimePersistence,
        project_control: ProjectControlAdapter,
        retrieval: RetrievalAdapter,
        local_ai: LocalAIAdapter,
        providers: ProviderAdapter,
        corpus_manager=None,
        background_worker=None,
        job_queue=None,
        cache_registry=None,
        telemetry_registry=None,
        recovery_coordinator=None,
        evaluation_view=None,
    ) -> None:
        self._registrations = registrations
        self._state_machine = state_machine
        self._persistence = persistence
        self._project_control = project_control
        self._retrieval = retrieval
        self._local_ai = local_ai
        self._providers = providers
        self._corpus_manager = corpus_manager
        self._background_worker = background_worker
        self._job_queue = job_queue
        self._cache_registry = cache_registry
        self._telemetry_registry = telemetry_registry
        self._recovery_coordinator = recovery_coordinator
        self._evaluation_view = evaluation_view

    # -- lifecycle -----------------------------------------------------

    def initialize(self) -> RuntimeReadiness:
        # Not yet persisted: no subsystem (including whichever one bootstraps
        # the schema, e.g. the analysis repository) has run initialize() yet,
        # so durable storage for this event cannot be assumed to exist.
        self._state_machine.transition(RuntimeState.INITIALIZING, trigger="startup", persist=False)
        for registration in sorted(self._registrations, key=lambda item: item.init_order):
            registration.subsystem.initialize()
        if self._recovery_coordinator is not None:
            # Matches the pre-Phase-8 lifespan()'s unconditional
            # project_coordinator.recover_expired_leases() call on every
            # startup -- run once here, inline, before computing readiness.
            # This is startup recovery, not the RECOVERING runtime state
            # (which is reserved for a previously-READY/DEGRADED system
            # handling a failure while serving traffic). Deliberately
            # excludes ProviderAdapter: its recover() calls
            # admission_preview(), which probes capability_report() as a
            # side effect when no cached report is passed -- that must stay
            # an explicit action (via RuntimeManager.recover()), not an
            # implicit one on every cold start. ProviderAdapter is also
            # registered in self._registrations (for its init/shutdown
            # lifecycle), so it must be filtered out here explicitly, not
            # just omitted from the extend() below.
            subsystems = [
                registration.subsystem for registration in self._registrations
                if registration.subsystem is not self._providers
            ]
            subsystems.extend([self._project_control, self._retrieval, self._local_ai])
            self._recovery_coordinator.run_recovery(subsystems)
        if self._background_worker is not None:
            self._background_worker.start()
        readiness = self.readiness()
        self._state_machine.transition(
            readiness.state,
            trigger="startup_complete" if readiness.ready else "startup_incomplete",
            reason=None if readiness.ready else "; ".join(readiness.blocking_reasons),
        )
        return readiness

    def shutdown(self) -> None:
        self._state_machine.transition(RuntimeState.STOPPING, trigger="shutdown_requested")
        if self._background_worker is not None:
            self._background_worker.stop()
        if self._telemetry_registry is not None:
            self._telemetry_registry.flush()
        for registration in sorted(
            self._registrations, key=lambda item: item.init_order, reverse=True
        ):
            registration.subsystem.shutdown()
        self._state_machine.transition(RuntimeState.STOPPED, trigger="shutdown_complete")

    # -- readiness / health ---------------------------------------------

    def readiness(self, *, project_id: str | None = None) -> RuntimeReadiness:
        blocking: list[str] = []

        control_ready = self._project_control.ready()
        if not control_ready:
            blocking.append("project_control_not_ready")

        retrieval_ready = self._retrieval.ready()
        if not retrieval_ready:
            blocking.append("project_retrieval_not_ready")

        corpus_valid: bool | None = None
        if project_id is not None and self._corpus_manager is not None:
            corpus_valid = self._corpus_manager.is_valid(project_id)
            if not corpus_valid:
                blocking.append("corpus_not_valid")

        providers_health = self._providers.health()
        providers_healthy = providers_health.status == CapabilityStatus.READY
        if not providers_healthy:
            blocking.append("providers_not_healthy")

        pending_recovery = (
            bool(self._recovery_coordinator.pending())
            if self._recovery_coordinator is not None
            else False
        )
        if pending_recovery:
            blocking.append("recovery_pending")

        ready = not blocking
        state = RuntimeState.READY if ready else RuntimeState.DEGRADED
        return RuntimeReadiness(
            ready=ready,
            state=state,
            blocking_reasons=tuple(blocking),
            control_ready=control_ready,
            retrieval_ready=retrieval_ready,
            corpus_valid=corpus_valid,
            providers_healthy=providers_healthy,
            pending_recovery=pending_recovery,
            project_id=project_id,
            generated_at=_now(),
        )

    def health(self) -> RuntimeHealthReport:
        # Probe hardware once and thread the same report through every
        # adapter that needs it, rather than re-probing per subsystem.
        host_report = self._local_ai.capability_report()
        subsystems = [self._project_control.health(), self._retrieval.health()]
        subsystems.append(self._local_ai.health(report=host_report))
        subsystems.extend(self._providers.detailed_health())
        for registration in self._registrations:
            subsystem = registration.subsystem
            if subsystem in (self._project_control, self._retrieval, self._local_ai, self._providers):
                continue
            health_method = getattr(subsystem, "health", None)
            if callable(health_method):
                subsystems.append(health_method())
        overall = (
            CapabilityStatus.READY
            if all(item.status == CapabilityStatus.READY for item in subsystems)
            else CapabilityStatus.UNAVAILABLE
        )
        return RuntimeHealthReport(
            runtime_state=self._state_machine.state,
            overall=overall,
            subsystems=tuple(subsystems),
            generated_at=_now(),
        )

    def snapshot(self) -> RuntimeSnapshot:
        return RuntimeSnapshot(
            state=self._state_machine.state,
            recent_transitions=self._state_machine.recent_transitions(),
            generated_at=_now(),
        )

    @property
    def state(self) -> RuntimeState:
        return self._state_machine.state

    # -- recovery ---------------------------------------------------------

    def recover(self) -> RuntimeReadiness:
        """Deterministic recovery pass: transitions to RECOVERING, calls
        every registered subsystem's own existing recover() (see
        recovery.py -- this never reimplements subsystem-specific recovery
        logic), then recomputes readiness and transitions to READY/DEGRADED.
        Recovery never violates replay: every underlying recover() call is
        itself idempotent (see recovery.py's docstring).
        """
        if self._recovery_coordinator is None:
            return self.readiness()
        current = self._state_machine.state
        if current not in (RuntimeState.READY, RuntimeState.DEGRADED):
            return self.readiness()
        self._state_machine.transition(RuntimeState.RECOVERING, trigger="recovery_started")
        subsystems = [registration.subsystem for registration in self._registrations]
        subsystems.extend([self._project_control, self._retrieval, self._local_ai, self._providers])
        self._recovery_coordinator.run_recovery(subsystems)
        readiness = self.readiness()
        self._state_machine.transition(
            readiness.state,
            trigger="recovery_complete",
            reason=None if readiness.ready else "; ".join(readiness.blocking_reasons),
        )
        return readiness

    # -- telemetry / corpus / cache / jobs (thin reads) --------------------

    def telemetry(self) -> RuntimeTelemetrySnapshot:
        if self._telemetry_registry is None:
            return RuntimeTelemetrySnapshot(generated_at=_now())
        return self._telemetry_registry.snapshot()

    def corpus_status(self, project_id: str) -> RuntimeCorpusStatus | None:
        if self._corpus_manager is None:
            return None
        freshness = self._corpus_manager.check_freshness(project_id)
        return RuntimeCorpusStatus(
            project_id=project_id,
            freshness=freshness,
            reindex_scheduled=not freshness.fresh,
            generated_at=_now(),
        )

    def cache_status(self) -> RuntimeCacheStatus:
        if self._cache_registry is None:
            return RuntimeCacheStatus(caches=(), generated_at=_now())
        return RuntimeCacheStatus(caches=self._cache_registry.all_statistics(), generated_at=_now())

    def jobs_status(self, *, limit: int = 20) -> RuntimeJobsStatus:
        if self._job_queue is None:
            return RuntimeJobsStatus(queued=0, claimed=0, running=0, generated_at=_now())
        summary = self._job_queue.status_summary(recent_limit=limit)
        return RuntimeJobsStatus(
            queued=summary["queued"],
            claimed=summary["claimed"],
            running=summary["running"],
            recent=summary["recent"],
            generated_at=_now(),
        )
