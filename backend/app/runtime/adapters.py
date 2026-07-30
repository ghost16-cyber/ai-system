from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable
from uuid import uuid4

from backend.app.local_ai.contracts import CapabilityStatus
from backend.app.local_ai.service import LocalAIService
from backend.app.project_coordinator import ProjectCoordinatorService
from backend.app.project_control.service import ProjectControlPlane
from backend.app.project_retrieval import ProjectRetrievalService, rag_provider_capabilities
from backend.app.runtime.contracts import RecoveryEvent, RuntimeSubsystemHealth

_HOST_CAPABILITY_MAX_AGE_SECONDS = 60


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SimpleInitAdapter:
    """Generic adapter for a singleton that only has a plain `.initialize()`
    method today (most of the pre-Phase-8 singletons: repository, artifact
    store, worker queue, mutation engine, synthesis proposal store, job
    queue). It never modifies the wrapped object -- it only calls its
    existing public `initialize()` (and, if present, an existing recovery
    method) and tracks its own `_initialized` flag to answer `ready()`.
    """

    def __init__(
        self,
        subsystem_id: str,
        target: Any,
        *,
        recover_method: Callable[[], int] | None = None,
    ) -> None:
        self.subsystem_id = subsystem_id
        self._target = target
        self._recover_method = recover_method
        self._initialized = False

    def initialize(self) -> None:
        self._target.initialize()
        self._initialized = True

    def shutdown(self) -> None:
        self._initialized = False

    def ready(self) -> bool:
        return self._initialized

    def health(self) -> RuntimeSubsystemHealth:
        return RuntimeSubsystemHealth(
            subsystem_id=self.subsystem_id,
            capability_id=self.subsystem_id,
            status=CapabilityStatus.READY if self._initialized else CapabilityStatus.UNAVAILABLE,
            probed_at=_now(),
        )

    def recover(self) -> RecoveryEvent | None:
        if self._recover_method is None:
            return None
        recovered = self._recover_method()
        if not recovered:
            return None
        return RecoveryEvent(
            recovery_id=str(uuid4()),
            failure_class="expired_lease_or_job",
            subsystem_id=self.subsystem_id,
            action="recover_expired",
            outcome=f"recovered {recovered}",
            occurred_at=_now(),
        )


class ProjectControlAdapter:
    """Wraps ProjectControlPlane -- the sole project lifecycle authority.
    This adapter never calls a mutating/authority method; it only calls the
    existing `initialize()` and tracks readiness. ProjectControlPlane is
    event-sourced and self-healing, so `recover()` is a no-op by design.
    """

    subsystem_id = "project_control"

    def __init__(self, project_control: ProjectControlPlane) -> None:
        self._project_control = project_control
        self._initialized = False

    def initialize(self) -> None:
        self._project_control.initialize()
        self._initialized = True

    def shutdown(self) -> None:
        self._initialized = False

    def ready(self) -> bool:
        return self._initialized

    def health(self) -> RuntimeSubsystemHealth:
        return RuntimeSubsystemHealth(
            subsystem_id=self.subsystem_id,
            capability_id=self.subsystem_id,
            status=CapabilityStatus.READY if self._initialized else CapabilityStatus.UNAVAILABLE,
            probed_at=_now(),
        )

    def recover(self) -> RecoveryEvent | None:
        return None


class RetrievalAdapter:
    """Wraps ProjectRetrievalService. Subsystem-level health/readiness here is
    process-level only (is the service initialized); project-scoped corpus
    freshness/validity is CorpusManager's responsibility, not this adapter's
    -- `status()`/`providers()` require a project_id and are read per-project
    by CorpusManager, never by this adapter.
    """

    subsystem_id = "project_retrieval"

    def __init__(self, project_retrieval_service: ProjectRetrievalService) -> None:
        self._service = project_retrieval_service
        self._initialized = False

    def initialize(self) -> None:
        self._service.initialize()
        self._initialized = True

    def shutdown(self) -> None:
        self._initialized = False

    def ready(self) -> bool:
        return self._initialized

    def health(self) -> RuntimeSubsystemHealth:
        return RuntimeSubsystemHealth(
            subsystem_id=self.subsystem_id,
            capability_id=self.subsystem_id,
            status=CapabilityStatus.READY if self._initialized else CapabilityStatus.UNAVAILABLE,
            probed_at=_now(),
        )

    def recover(self) -> RecoveryEvent | None:
        return None


class LocalAIAdapter:
    """Wraps LocalAIService. `health()` calls the existing
    `capability_report()` exactly once per call (RuntimeManager.health()
    threads the same `HostCapabilityReport` through other adapters via
    `report=` rather than re-probing hardware per subsystem -- see
    manager.py). `recover()` delegates to the existing
    `recover_expired_jobs()`; it never re-implements GPU/VRAM admission.
    """

    subsystem_id = "local_ai"

    def __init__(self, local_ai_service: LocalAIService) -> None:
        self._service = local_ai_service
        self._initialized = False

    def initialize(self) -> None:
        self._service.initialize()
        self._initialized = True

    def shutdown(self) -> None:
        self._initialized = False

    def ready(self) -> bool:
        return self._initialized

    def capability_report(self, *, max_age_seconds: int = _HOST_CAPABILITY_MAX_AGE_SECONDS):
        """Public accessor for the underlying service's capability report, so
        RuntimeManager can probe hardware once and thread the same report
        through this adapter's `health(report=...)` rather than reaching into
        a private attribute."""
        return self._service.capability_report(max_age_seconds=max_age_seconds)

    # Capabilities whose absence is a normal, non-degrading state (optional
    # future backends, or explicitly policy-disabled) -- only the host's
    # fundamental capabilities gate the local_ai subsystem's own health.
    _CORE_CAPABILITY_IDS = frozenset({"cpu", "memory"})
    _NON_DEGRADING_STATUSES = frozenset({
        CapabilityStatus.READY,
        CapabilityStatus.AVAILABLE,
        CapabilityStatus.INTENTIONALLY_DISABLED,
        CapabilityStatus.NOT_CONFIGURED,
    })

    def health(self, *, report: Any | None = None) -> RuntimeSubsystemHealth:
        if not self._initialized:
            return RuntimeSubsystemHealth(
                subsystem_id=self.subsystem_id,
                capability_id=self.subsystem_id,
                status=CapabilityStatus.UNAVAILABLE,
                probed_at=_now(),
            )
        host_report = report or self._service.capability_report(
            max_age_seconds=_HOST_CAPABILITY_MAX_AGE_SECONDS
        )
        core_unavailable = tuple(
            capability
            for capability in host_report.capabilities
            if capability.capability_id in self._CORE_CAPABILITY_IDS
            and capability.status not in self._NON_DEGRADING_STATUSES
        )
        degraded = tuple(
            capability
            for capability in host_report.capabilities
            if capability.status not in self._NON_DEGRADING_STATUSES
        )
        return RuntimeSubsystemHealth(
            subsystem_id=self.subsystem_id,
            capability_id=self.subsystem_id,
            status=CapabilityStatus.UNAVAILABLE if core_unavailable else CapabilityStatus.READY,
            details={
                "report_id": host_report.report_id,
                "degraded_capabilities": [item.capability_id for item in degraded],
            },
            reason=(
                f"{len(core_unavailable)} core capability(ies) unavailable"
                if core_unavailable else None
            ),
            probed_at=_now(),
        )

    def recover(self) -> RecoveryEvent | None:
        recovered = self._service.recover_expired_jobs()
        if not recovered:
            return None
        return RecoveryEvent(
            recovery_id=str(uuid4()),
            failure_class="expired_local_ai_job",
            subsystem_id=self.subsystem_id,
            action="recover_expired_jobs",
            outcome=f"recovered {recovered}",
            occurred_at=_now(),
        )


class ProjectCoordinatorAdapter:
    """Wraps ProjectCoordinatorService. `recover()` delegates to the existing
    `recover_expired_leases()` -- the same call the pre-Phase-8 `lifespan()`
    already made after startup.
    """

    subsystem_id = "project_coordinator"

    def __init__(self, project_coordinator: ProjectCoordinatorService) -> None:
        self._coordinator = project_coordinator
        self._initialized = False

    def initialize(self) -> None:
        self._coordinator.initialize()
        self._initialized = True

    def shutdown(self) -> None:
        self._initialized = False

    def ready(self) -> bool:
        return self._initialized

    def health(self) -> RuntimeSubsystemHealth:
        return RuntimeSubsystemHealth(
            subsystem_id=self.subsystem_id,
            capability_id=self.subsystem_id,
            status=CapabilityStatus.READY if self._initialized else CapabilityStatus.UNAVAILABLE,
            probed_at=_now(),
        )

    def recover(self) -> RecoveryEvent | None:
        recovered = self._coordinator.recover_expired_leases()
        if not recovered:
            return None
        return RecoveryEvent(
            recovery_id=str(uuid4()),
            failure_class="expired_coordinator_lease",
            subsystem_id=self.subsystem_id,
            action="recover_expired_leases",
            outcome=f"recovered {recovered}",
            occurred_at=_now(),
        )


class ProviderAdapter:
    """Owns lazy-load/unload/device-selection bookkeeping for the learned RAG
    providers (embedding + reranker). Health is derived from the shared
    `rag_provider_capabilities()` (the same bounded, weights-free probe
    already registered with LocalAIService via
    `set_additional_capability_probe` -- this adapter does not add a second
    probe). GPU/VRAM recovery routes through
    `local_ai_service.admission_preview(...)`; this adapter never computes
    VRAM headroom itself.
    """

    subsystem_id = "rag_providers"

    def __init__(self, embedding: Any, reranker: Any, local_ai_service: LocalAIService) -> None:
        self._embedding = embedding
        self._reranker = reranker
        self._local_ai_service = local_ai_service
        self._initialized = False

    def initialize(self) -> None:
        # Providers are constructed eagerly by build_retrieval_providers();
        # this adapter only marks the subsystem as registered/observable.
        self._initialized = True

    def shutdown(self) -> None:
        self._initialized = False

    def ready(self) -> bool:
        return self._initialized

    def detailed_health(self) -> tuple[RuntimeSubsystemHealth, ...]:
        capabilities = rag_provider_capabilities(self._embedding, self._reranker)
        return tuple(
            RuntimeSubsystemHealth(
                **capability.model_dump(exclude={"schema_version"}),
                subsystem_id=capability.capability_id,
            )
            for capability in capabilities
        )

    def health(self) -> RuntimeSubsystemHealth:
        detailed = self.detailed_health()
        if not detailed:
            status = CapabilityStatus.UNAVAILABLE if not self._initialized else CapabilityStatus.READY
        else:
            status = (
                CapabilityStatus.READY
                if all(item.status == CapabilityStatus.READY for item in detailed)
                else CapabilityStatus.UNAVAILABLE
            )
        return RuntimeSubsystemHealth(
            subsystem_id=self.subsystem_id,
            capability_id=self.subsystem_id,
            status=status,
            details={"providers": [item.capability_id for item in detailed]},
            probed_at=_now(),
        )

    def recover(self) -> RecoveryEvent | None:
        from backend.app.local_ai.contracts import AdmissionOutcome, HardwareAdmissionRequest

        decision = self._local_ai_service.admission_preview(HardwareAdmissionRequest(
            workload_class="rag_learned_retrieval",
            model_profile_id="rag-learned-provider",
            estimated_model_bytes=256 * 1024**2,
            requested_context=512,
            estimated_kv_bytes_per_token=0,
            requested_output_tokens=1,
            allow_cpu_fallback=True,
            prefer_gpu=True,
        ))
        blocked = decision.outcome in {
            AdmissionOutcome.BLOCKED_VRAM,
            AdmissionOutcome.BLOCKED_RAM,
            AdmissionOutcome.BLOCKED_PROVIDER,
            AdmissionOutcome.BLOCKED_DEPENDENCY,
        }
        if not blocked:
            return None
        return RecoveryEvent(
            recovery_id=str(uuid4()),
            failure_class="provider_admission_blocked",
            subsystem_id=self.subsystem_id,
            action="admission_preview",
            outcome=decision.outcome.value,
            occurred_at=_now(),
        )
