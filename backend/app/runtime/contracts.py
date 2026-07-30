from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field

from backend.app.local_ai.contracts import Capability, CapabilityStatus
from backend.app.project_control.contracts import StrictModel


class RuntimeState(StrEnum):
    STOPPED = "stopped"
    INITIALIZING = "initializing"
    READY = "ready"
    DEGRADED = "degraded"
    RECOVERING = "recovering"
    STOPPING = "stopping"


class RuntimeStateTransition(StrictModel):
    schema_version: Literal["astra.runtime.state-transition.v1"] = "astra.runtime.state-transition.v1"
    from_state: RuntimeState
    to_state: RuntimeState
    trigger: str
    reason: str | None = None
    occurred_at: datetime


class RuntimeSubsystemHealth(Capability):
    """A `Capability` (reused verbatim) plus the subsystem_id it describes.

    RuntimeManager never re-probes hardware or re-implements readiness logic;
    every RuntimeSubsystemHealth is built from an existing subsystem's own
    read methods (see adapters.py).
    """

    schema_version: Literal["astra.runtime.subsystem-health.v1"] = "astra.runtime.subsystem-health.v1"
    subsystem_id: str


class RuntimeHealthReport(StrictModel):
    schema_version: Literal["astra.runtime.health-report.v1"] = "astra.runtime.health-report.v1"
    runtime_state: RuntimeState
    overall: CapabilityStatus
    subsystems: tuple[RuntimeSubsystemHealth, ...]
    generated_at: datetime


class RuntimeReadiness(StrictModel):
    schema_version: Literal["astra.runtime.readiness.v1"] = "astra.runtime.readiness.v1"
    ready: bool
    state: RuntimeState
    blocking_reasons: tuple[str, ...] = ()
    control_ready: bool
    retrieval_ready: bool
    corpus_valid: bool | None = None
    providers_healthy: bool
    pending_recovery: bool
    project_id: str | None = None
    generated_at: datetime


class BackgroundJobStatus(StrEnum):
    QUEUED = "queued"
    CLAIMED = "claimed"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class BackgroundJobResult(StrictModel):
    schema_version: Literal["astra.runtime.background-job-result.v1"] = (
        "astra.runtime.background-job-result.v1"
    )
    succeeded: bool
    error: str | None = Field(default=None, max_length=500)


class BackgroundJob(StrictModel):
    schema_version: Literal["astra.runtime.background-job.v1"] = "astra.runtime.background-job.v1"
    job_id: str
    idempotency_key: str
    job_type: str
    target_id: str
    status: BackgroundJobStatus
    priority: int
    payload: dict = Field(default_factory=dict)
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    result: BackgroundJobResult | None = None
    created_at: datetime
    updated_at: datetime


class RuntimeJobsStatus(StrictModel):
    schema_version: Literal["astra.runtime.jobs-status.v1"] = "astra.runtime.jobs-status.v1"
    queued: int = Field(ge=0)
    claimed: int = Field(ge=0)
    running: int = Field(ge=0)
    recent: tuple[BackgroundJob, ...] = ()
    generated_at: datetime


class CacheStatistics(StrictModel):
    schema_version: Literal["astra.runtime.cache-statistics.v1"] = "astra.runtime.cache-statistics.v1"
    cache_id: str
    hits: int = Field(ge=0)
    misses: int = Field(ge=0)
    evictions: int = Field(ge=0)
    size: int = Field(ge=0)
    version_tag: str | None = None


class RuntimeCacheStatus(StrictModel):
    schema_version: Literal["astra.runtime.cache-status.v1"] = "astra.runtime.cache-status.v1"
    caches: tuple[CacheStatistics, ...]
    generated_at: datetime


class CorpusFreshness(StrictModel):
    schema_version: Literal["astra.runtime.corpus-freshness.v1"] = "astra.runtime.corpus-freshness.v1"
    project_id: str
    fresh: bool
    reason: str | None = None
    repository_changed: bool = False
    manifest_changed: bool = False
    files_deleted: int = Field(default=0, ge=0)
    files_renamed: int = Field(default=0, ge=0)
    chunks_changed: int = Field(default=0, ge=0)
    current_generation_id: str | None = None
    checked_at: datetime


class RuntimeCorpusStatus(StrictModel):
    schema_version: Literal["astra.runtime.corpus-status.v1"] = "astra.runtime.corpus-status.v1"
    project_id: str
    freshness: CorpusFreshness
    reindex_scheduled: bool
    generated_at: datetime


class RuntimeTelemetrySnapshot(StrictModel):
    schema_version: Literal["astra.runtime.telemetry-snapshot.v1"] = "astra.runtime.telemetry-snapshot.v1"
    counters: dict[str, int] = Field(default_factory=dict)
    gauges: dict[str, float] = Field(default_factory=dict)
    generated_at: datetime


class RecoveryEvent(StrictModel):
    schema_version: Literal["astra.runtime.recovery-event.v1"] = "astra.runtime.recovery-event.v1"
    recovery_id: str
    failure_class: str
    subsystem_id: str
    action: str
    outcome: str
    occurred_at: datetime


class RuntimeSnapshot(StrictModel):
    schema_version: Literal["astra.runtime.snapshot.v1"] = "astra.runtime.snapshot.v1"
    state: RuntimeState
    recent_transitions: tuple[RuntimeStateTransition, ...]
    generated_at: datetime
