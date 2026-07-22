from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field

from backend.app.project_control.contracts import StrictModel


class CapabilityStatus(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    INSTALLED_UNUSABLE = "installed_but_unusable"
    VERSION_UNSUPPORTED = "version_unsupported"
    DEVICE_INACCESSIBLE = "device_inaccessible"
    PERMISSION_DENIED = "permission_denied"
    INSUFFICIENT_MEMORY = "insufficient_memory"
    NOT_CONFIGURED = "not_configured"
    INTENTIONALLY_DISABLED = "intentionally_disabled"
    UNKNOWN = "unknown"


class AdmissionOutcome(StrEnum):
    GPU = "admitted_on_gpu"
    CPU = "admitted_on_cpu"
    REDUCED_CONTEXT = "admitted_with_reduced_context"
    QUEUED = "queued"
    BLOCKED_VRAM = "blocked_due_to_vram"
    BLOCKED_RAM = "blocked_due_to_ram"
    BLOCKED_PROVIDER = "blocked_due_to_provider"
    BLOCKED_DEPENDENCY = "blocked_due_to_missing_dependency"
    BLOCKED_POLICY = "blocked_by_policy"
    USER_APPROVAL = "requires_user_approval"


class SchedulerStatus(StrEnum):
    QUEUED = "queued"
    CLAIMED = "claimed"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    BLOCKED = "blocked"


class Capability(StrictModel):
    schema_version: Literal["astra.local-ai.capability.v1"] = "astra.local-ai.capability.v1"
    capability_id: str
    status: CapabilityStatus
    version: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    reason: str | None = None
    remediation_commands: tuple[str, ...] = ()
    probed_at: datetime
    provenance: dict[str, Any] = Field(default_factory=dict)


class CPUCapability(Capability):
    model: str | None = None
    architecture: str
    logical_cores: int = Field(ge=1)
    physical_cores: int | None = Field(default=None, ge=1)


class MemoryCapability(Capability):
    total_bytes: int | None = Field(default=None, ge=0)
    available_bytes: int | None = Field(default=None, ge=0)
    estimate: bool = False


class CUDACapability(Capability):
    driver_visible: bool = False
    runtime_visible: bool = False
    runtime_version: str | None = None


class GPUCapability(Capability):
    device_count: int = Field(default=0, ge=0)
    device_names: tuple[str, ...] = ()
    device_identities: tuple[str, ...] = ()
    devices: tuple[dict[str, Any], ...] = ()
    driver_version: str | None = None


class VRAMCapability(Capability):
    total_bytes: int | None = Field(default=None, ge=0)
    free_bytes: int | None = Field(default=None, ge=0)
    estimate: bool = False


class PyTorchCapability(Capability):
    installed: bool = False
    cuda_available: bool = False
    cuda_version: str | None = None
    device_count: int = Field(default=0, ge=0)


class OllamaCapability(Capability):
    endpoint: str
    configured_models: tuple[str, ...] = ()
    installed_models: tuple[str, ...] = ()
    loaded_models: tuple[str, ...] = ()
    provider_reachable: bool = False
    configured_model_missing: bool = False


class ONNXRuntimeCapability(Capability):
    installed: bool = False


class TensorRTCapability(Capability):
    installed: bool = False


class EmbeddingCapability(Capability):
    enabled: bool = False


class TrainingCapability(Capability):
    enabled: bool = False


class ProviderCapability(Capability):
    provider_id: str
    provider_type: str


class ModelCapability(Capability):
    model_profile_id: str
    local_available: bool = False


class ExecutionBackendCapability(Capability):
    backend: str
    devices: tuple[str, ...] = ()


CapabilityRecord = (
    CPUCapability | MemoryCapability | CUDACapability | GPUCapability |
    VRAMCapability | PyTorchCapability | OllamaCapability |
    ONNXRuntimeCapability | TensorRTCapability | EmbeddingCapability |
    TrainingCapability | ProviderCapability | ModelCapability |
    ExecutionBackendCapability | Capability
)


class CapabilityPolicyResult(StrictModel):
    schema_version: Literal["astra.local-ai.capability-policy.v1"] = "astra.local-ai.capability-policy.v1"
    allowed: bool
    reason: str
    warnings: tuple[str, ...] = ()


class HardwareAdmissionRequest(StrictModel):
    schema_version: Literal["astra.local-ai.admission-request.v1"] = "astra.local-ai.admission-request.v1"
    workload_class: str
    model_profile_id: str
    estimated_model_bytes: int = Field(ge=0)
    requested_context: int = Field(ge=1)
    estimated_kv_bytes_per_token: int = Field(default=131072, ge=0)
    requested_output_tokens: int = Field(default=1024, ge=1)
    allow_cpu_fallback: bool = False
    prefer_gpu: bool = True
    concurrent_gpu_jobs: int = Field(default=0, ge=0)


class HardwareAdmissionDecision(StrictModel):
    schema_version: Literal["astra.local-ai.admission-decision.v1"] = "astra.local-ai.admission-decision.v1"
    outcome: AdmissionOutcome
    reason: str
    backend: str | None = None
    device: str | None = None
    admitted_context: int | None = None
    estimated_required_bytes: int = Field(ge=0)
    available_bytes: int | None = Field(default=None, ge=0)
    safety_reserve_bytes: int = Field(ge=0)
    estimates_are_approximate: bool = True
    requires_explicit_approval: bool = False


class RuntimeUnavailabilityReason(StrictModel):
    schema_version: Literal["astra.local-ai.unavailability.v1"] = "astra.local-ai.unavailability.v1"
    category: CapabilityStatus
    component: str
    message: str
    remediation_commands: tuple[str, ...] = ()


class HostCapabilityReport(StrictModel):
    schema_version: Literal["astra.local-ai.host-report.v1"] = "astra.local-ai.host-report.v1"
    report_id: str
    os_name: str
    architecture: str
    python_version: str
    wsl: bool
    capabilities: tuple[CapabilityRecord, ...]
    disk: dict[str, Any] = Field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    generated_at: datetime


class ProviderProfile(StrictModel):
    schema_version: Literal["astra.local-ai.provider.v1"] = "astra.local-ai.provider.v1"
    provider_id: str
    provider_type: str
    endpoint_identity: str
    health_status: CapabilityStatus
    supported_model_ids: tuple[str, ...] = ()
    input_modalities: tuple[str, ...] = ("text",)
    context_limit: int | None = None
    output_limit: int | None = None
    streaming: bool = False
    structured_output: bool = False
    tool_calls: bool = False
    supports_cancellation: bool = True
    timeout_seconds: int = Field(default=120, ge=1, le=3600)
    execution_backend: str
    capability_requirements: tuple[str, ...] = ()
    credential_required: bool = False
    provider_version: str | None = None
    enabled: bool = False
    provenance: dict[str, Any] = Field(default_factory=dict)


class ModelProfile(StrictModel):
    schema_version: Literal["astra.local-ai.model-profile.v1"] = "astra.local-ai.model-profile.v1"
    model_profile_id: str
    provider_id: str
    provider_model_id: str
    display_name: str
    model_family: str
    parameter_scale: str
    quantization: str | None = None
    intended_roles: tuple[str, ...] = ()
    thinking_support: bool = False
    non_thinking_support: bool = True
    coding_support: bool = False
    structured_output: bool = False
    tool_calls: bool = False
    context_window: int = Field(ge=1)
    operational_context: int = Field(ge=1)
    maximum_output_tokens: int = Field(ge=1)
    estimated_model_bytes: int = Field(ge=0)
    minimum_ram_bytes: int = Field(ge=0)
    minimum_vram_bytes: int = Field(ge=0)
    cpu_support: bool = True
    gpu_support: bool = True
    license_metadata: dict[str, Any] = Field(default_factory=dict)
    source_metadata: dict[str, Any] = Field(default_factory=dict)
    local_available: bool = False
    policy_status: CapabilityStatus = CapabilityStatus.NOT_CONFIGURED
    enabled: bool = False
    default_roles: tuple[str, ...] = ()
    prompt_template_profile: str
    stop_sequences: tuple[str, ...] = ()
    reasoning_content_handling: Literal["discard", "provider_only"] = "discard"
    provenance: dict[str, Any] = Field(default_factory=dict)


class ResourceRequest(StrictModel):
    backend: str
    cpu_slots: int = Field(default=1, ge=1)
    gpu_exclusive: bool = False
    estimated_ram_bytes: int = Field(default=0, ge=0)
    estimated_vram_bytes: int = Field(default=0, ge=0)


class SchedulerJob(StrictModel):
    schema_version: Literal["astra.local-ai.scheduler-job.v1"] = "astra.local-ai.scheduler-job.v1"
    queue_id: str
    job_id: str
    project_run_id: str | None = None
    conversation_id: str | None = None
    coordinator_intent_id: str | None = None
    workload_class: str
    resource_request: ResourceRequest
    admission: HardwareAdmissionDecision
    status: SchedulerStatus
    idempotency_key: str
    request_hash: str
    priority: int = Field(default=100, ge=0, le=1000)
    queue_position: int | None = Field(default=None, ge=1)
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: RuntimeUnavailabilityReason | None = None


class ExecutionProvenance(StrictModel):
    schema_version: Literal["astra.local-ai.execution-provenance.v1"] = "astra.local-ai.execution-provenance.v1"
    execution_id: str
    project_run_id: str | None = None
    conversation_id: str | None = None
    coordinator_intent_id: str | None = None
    provider_id: str
    provider_endpoint_identity: str = "unspecified"
    model_profile_id: str
    provider_model_id: str
    prompt_template_version: str
    input_artifact_hashes: tuple[str, ...] = ()
    evidence_hashes: tuple[str, ...] = ()
    configuration: dict[str, Any] = Field(default_factory=dict)
    context_limit: int
    output_limit: int
    thinking_mode: bool
    backend: str
    device: str
    admission: HardwareAdmissionDecision
    started_at: datetime
    completed_at: datetime | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    timed_out: bool = False
    cancelled: bool = False
    response_hash: str | None = None
    validation_result: dict[str, Any] = Field(default_factory=dict)
    retry_identity: str
    replay_identity: str | None = None
    error_category: str | None = None
    redacted_diagnostics: dict[str, Any] = Field(default_factory=dict)
    reasoning_content_persisted: Literal[False] = False


# Disabled future-work contracts. Content represented by these records is always
# untrusted evidence and cannot grant lifecycle authority.
class CorpusSource(StrictModel):
    source_id: str
    source_type: Literal["archive", "repository", "document"]
    display_name: str
    license_metadata: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = False
    provenance: dict[str, Any] = Field(default_factory=dict)


class ArchiveSource(CorpusSource):
    source_type: Literal["archive"] = "archive"
    archive_name: str


class RepositorySource(CorpusSource):
    source_type: Literal["repository"] = "repository"
    repository_identity: str


class RAGDocument(StrictModel):
    document_id: str
    source_id: str
    content_hash: str
    media_type: str
    provenance: dict[str, Any]


class RAGChunk(StrictModel):
    chunk_id: str
    document_id: str
    content_hash: str
    ordinal: int = Field(ge=0)
    provenance: dict[str, Any]


class ChunkProvenance(StrictModel):
    source_id: str
    document_id: str
    member_path: str | None = None
    content_hash: str
    license_id: str | None = None


class LicenseMetadata(StrictModel):
    license_id: str | None = None
    verified: bool = False
    source: str | None = None


class ExclusionDecision(StrictModel):
    included: bool
    reason: str
    policy_version: str


class RetrievalCitation(StrictModel):
    chunk_id: str
    source_id: str
    content_hash: str
    display_reference: str


class PoisonedContentWarning(StrictModel):
    detected: bool
    categories: tuple[str, ...] = ()
    content_remains_untrusted: Literal[True] = True


class StaleIndexState(StrictModel):
    stale: bool
    current_source_manifest_hash: str | None = None
    indexed_source_manifest_hash: str | None = None


class IngestionPolicy(StrictModel):
    enabled: Literal[False] = False
    license_allowlist: tuple[str, ...] = ()
    excluded_patterns: tuple[str, ...]
    secret_scan_required: bool = True


class EmbeddingRequest(StrictModel):
    request_id: str
    chunk_hashes: tuple[str, ...]
    provider_id: str


class EmbeddingResult(StrictModel):
    request_id: str
    status: CapabilityStatus
    vector_count: int = Field(default=0, ge=0)


class IndexRevision(StrictModel):
    index_revision_id: str
    source_manifest_hash: str
    enabled: Literal[False] = False
    stale: bool = True


class RetrievalRequest(StrictModel):
    request_id: str
    query_hash: str
    index_revision_id: str
    limit: int = Field(default=5, ge=1, le=20)


class RetrievalResult(StrictModel):
    request_id: str
    citations: tuple[dict[str, Any], ...] = ()
    poisoned_content_warning: bool = False
    stale_index: bool = True


class EvaluationDefinition(StrictModel):
    evaluation_id: str
    suite_version: str
    immutable_case_hashes: tuple[str, ...]
    holdout_split_identity: str
    prompt_version: str
    scoring_functions: tuple[str, ...]
    enabled: Literal[False] = False


class EvaluationCase(StrictModel):
    case_id: str
    immutable_hash: str
    prompt_version: str
    expected_schema: dict[str, Any]
    scoring_functions: tuple[str, ...]
    safety_expectations: dict[str, Any] = Field(default_factory=dict)


class EvaluationComparisonReport(StrictModel):
    report_id: str
    evaluation_id: str
    model_result_ids: tuple[str, ...]
    structured_output_validity: dict[str, float] = Field(default_factory=dict)
    coding_correctness: dict[str, float] = Field(default_factory=dict)
    safety_behavior: dict[str, float] = Field(default_factory=dict)
    tool_use_correctness: dict[str, float] = Field(default_factory=dict)
    prompt_injection_resistance: dict[str, float] = Field(default_factory=dict)
    latency_ms: dict[str, float] = Field(default_factory=dict)
    token_counts: dict[str, int] = Field(default_factory=dict)
    memory_observations: dict[str, Any] = Field(default_factory=dict)


class EvaluationResult(StrictModel):
    result_id: str
    evaluation_id: str
    model_profile_id: str
    metrics: dict[str, float] = Field(default_factory=dict)
    reproducibility: dict[str, Any] = Field(default_factory=dict)


class TrainingDefinition(StrictModel):
    training_definition_id: str
    dataset_manifest_hash: str
    split_manifest_hash: str
    holdout_manifest_hash: str
    configuration: dict[str, Any]
    adapter_configuration: dict[str, Any]
    enabled: Literal[False] = False


class DatasetManifest(StrictModel):
    dataset_manifest_id: str
    provenance_hash: str
    record_count: int = Field(ge=0)
    content_hash: str


class DatasetProvenance(StrictModel):
    provenance_id: str
    source_references: tuple[str, ...]
    licenses: tuple[LicenseMetadata, ...] = ()
    created_by: str


class TrainingSplitManifest(StrictModel):
    split_manifest_id: str
    dataset_manifest_id: str
    training_hashes: tuple[str, ...]
    validation_hashes: tuple[str, ...]


class HoldoutManifest(StrictModel):
    holdout_manifest_id: str
    dataset_manifest_id: str
    case_hashes: tuple[str, ...]


class TransformationRecord(StrictModel):
    transformation_id: str
    input_manifest_hash: str
    output_manifest_hash: str
    operations: tuple[str, ...]


class FilteringReport(StrictModel):
    report_id: str
    accepted: int = Field(ge=0)
    rejected: int = Field(ge=0)
    reasons: dict[str, int] = Field(default_factory=dict)


class SafetyScanReport(StrictModel):
    report_id: str
    secrets_found: int = Field(ge=0)
    unsafe_records: int = Field(ge=0)
    passed: bool


class TrainingConfiguration(StrictModel):
    backend: str
    base_model_profile_id: str
    epochs: float = Field(gt=0)
    maximum_steps: int | None = Field(default=None, ge=1)
    resource_policy: dict[str, Any]


class AdapterConfiguration(StrictModel):
    adapter_type: str
    rank: int = Field(ge=1)
    alpha: float = Field(gt=0)
    target_modules: tuple[str, ...]


class CheckpointProvenance(StrictModel):
    checkpoint_id: str
    training_run_id: str
    base_model_hash: str
    dataset_manifest_hash: str
    configuration_hash: str


class TrainingRun(StrictModel):
    training_run_id: str
    training_definition_id: str
    status: Literal["disabled", "cancelled", "failed"] = "disabled"
    checkpoint_provenance: dict[str, Any] = Field(default_factory=dict)
    resume_identity: str | None = None
    evaluation_linkage: str | None = None


__all__ = [name for name in globals() if not name.startswith("_")]
