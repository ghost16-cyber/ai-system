from __future__ import annotations

import json
import os
import platform
import shutil
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from urllib import error as url_error
from urllib import request as url_request
from uuid import uuid4

from backend.app.database.migrations import assert_schema_compatible
from backend.app.local_ai.contracts import (
    AdmissionOutcome,
    Capability,
    CapabilityStatus,
    CPUCapability,
    CUDACapability,
    ExecutionProvenance,
    GPUCapability,
    HardwareAdmissionDecision,
    HardwareAdmissionRequest,
    HostCapabilityReport,
    MemoryCapability,
    ModelProfile,
    OllamaCapability,
    ONNXRuntimeCapability,
    ProviderProfile,
    PyTorchCapability,
    ResourceRequest,
    RuntimeUnavailabilityReason,
    SchedulerJob,
    SchedulerStatus,
    TensorRTCapability,
    TrainingCapability,
    VRAMCapability,
)
from backend.app.local_ai.config import (
    LocalAIConfiguration,
    load_local_ai_configuration,
)
from backend.app.local_ai.hardware import (
    HardwareCapabilityRegistry,
    HardwareSnapshot,
    MemoryProbeResult,
    parse_linux_meminfo,
    probe_host_memory,
)
from backend.app.project_control.contracts import canonical_json, content_hash


Probe = Callable[[], tuple[Capability, ...]]
OllamaProbe = Callable[
    [LocalAIConfiguration], tuple[bool, tuple[str, ...], tuple[str, ...], str | None]
]


def _now() -> datetime:
    return datetime.now(timezone.utc)


_parse_linux_meminfo = parse_linux_meminfo
_probe_host_memory = probe_host_memory


def _probe_ollama(
    configuration: LocalAIConfiguration,
) -> tuple[bool, tuple[str, ...], tuple[str, ...], str | None]:
    """Read bounded Ollama state; never starts the daemon or pulls a model."""
    if configuration.provider_type != "ollama":
        return False, (), (), "configured_provider_is_not_ollama"
    try:
        installed = _ollama_model_names(
            f"{configuration.endpoint_identity}/api/tags",
            timeout=configuration.connection_timeout_seconds,
        )
    except (OSError, TimeoutError, ValueError, url_error.URLError):
        return False, (), (), "provider_unreachable"
    try:
        loaded = _ollama_model_names(
            f"{configuration.endpoint_identity}/api/ps",
            timeout=configuration.connection_timeout_seconds,
        )
    except (OSError, TimeoutError, ValueError, url_error.URLError):
        loaded = ()
    missing = tuple(
        model for model in configuration.configured_models if model not in installed
    )
    return (
        True,
        installed,
        loaded,
        "configured_model_missing" if missing else None,
    )


def _ollama_model_names(url: str, *, timeout: int) -> tuple[str, ...]:
    request = url_request.Request(url, method="GET")
    with url_request.urlopen(request, timeout=timeout) as response:
        payload = response.read(1024 * 1024 + 1)
    if len(payload) > 1024 * 1024:
        raise ValueError("ollama_response_too_large")
    parsed = json.loads(payload.decode("utf-8"))
    if not isinstance(parsed, dict) or not isinstance(parsed.get("models", []), list):
        raise ValueError("ollama_response_malformed")
    names: list[str] = []
    for item in parsed.get("models", []):
        if not isinstance(item, dict):
            raise ValueError("ollama_response_malformed")
        name = item.get("name") or item.get("model")
        if isinstance(name, str) and name and len(name) <= 300:
            names.append(name)
    return tuple(dict.fromkeys(names))


def default_model_profiles(
    configuration: LocalAIConfiguration | None = None,
) -> tuple[ModelProfile, ...]:
    configuration = configuration or load_local_ai_configuration()
    gib = 1024**3
    configured_roles = tuple(
        role
        for role in ("coding", "synthesis", "planning", "review")
        if configuration.model_for_role(role) == configuration.synthesis_model
    )
    return (
        ModelProfile(
            model_profile_id="configured-local-model",
            provider_id="ollama-local",
            provider_model_id=configuration.synthesis_model,
            display_name=f"Configured local model ({configuration.synthesis_model})",
            model_family=configuration.synthesis_model.split(":", 1)[0],
            parameter_scale="configured",
            intended_roles=configured_roles,
            coding_support=True,
            structured_output=True,
            context_window=max(32768, configuration.maximum_context_tokens),
            operational_context=configuration.maximum_context_tokens,
            maximum_output_tokens=configuration.maximum_output_tokens,
            estimated_model_bytes=2 * gib,
            minimum_ram_bytes=4 * gib,
            minimum_vram_bytes=2 * gib,
            prompt_template_profile="configured-local-coder-v1",
            policy_status=CapabilityStatus.NOT_CONFIGURED,
            source_metadata={
                "configuration_authority": configuration.schema_version,
                "tag_is_configurable": True,
                "local_tag_must_be_verified": True,
                "no_auto_start": True,
                "no_auto_pull": True,
            },
            default_roles=configured_roles,
        ),
        ModelProfile(
            model_profile_id="fake-deterministic",
            provider_id="fake-deterministic",
            provider_model_id="fake:deterministic",
            display_name="Deterministic fake model",
            model_family="fake",
            parameter_scale="0",
            intended_roles=("test",),
            structured_output=True,
            context_window=8192,
            operational_context=4096,
            maximum_output_tokens=1024,
            estimated_model_bytes=0,
            minimum_ram_bytes=0,
            minimum_vram_bytes=0,
            prompt_template_profile="fake-v1",
            policy_status=CapabilityStatus.AVAILABLE,
            local_available=True,
            enabled=True,
        ),
    )


def default_provider_profiles(
    configuration: LocalAIConfiguration | None = None,
) -> tuple[ProviderProfile, ...]:
    configuration = configuration or load_local_ai_configuration()
    return (
        ProviderProfile(
            provider_id="unavailable",
            provider_type="unavailable",
            endpoint_identity="none",
            health_status=CapabilityStatus.UNAVAILABLE,
            execution_backend="none",
        ),
        ProviderProfile(
            provider_id="fake-deterministic",
            provider_type="fake",
            endpoint_identity="in-process",
            health_status=CapabilityStatus.AVAILABLE,
            supported_model_ids=("fake:deterministic",),
            structured_output=True,
            execution_backend="cpu",
            enabled=True,
        ),
        ProviderProfile(
            provider_id="ollama-local",
            provider_type=configuration.provider_type,
            endpoint_identity=configuration.endpoint_identity,
            health_status=CapabilityStatus.NOT_CONFIGURED,
            supported_model_ids=configuration.configured_models,
            context_limit=configuration.maximum_context_tokens,
            output_limit=configuration.maximum_output_tokens,
            streaming=True,
            structured_output=True,
            tool_calls=True,
            execution_backend="ollama",
            enabled=False,
            timeout_seconds=configuration.generation_timeout_seconds,
            provenance={
                "configuration_authority": configuration.schema_version,
                "connection_timeout_seconds": configuration.connection_timeout_seconds,
                "no_auto_start": True,
                "no_auto_pull": True,
            },
        ),
        ProviderProfile(
            provider_id="onnx-future",
            provider_type="onnx",
            endpoint_identity="local",
            health_status=CapabilityStatus.INTENTIONALLY_DISABLED,
            execution_backend="onnx",
        ),
        ProviderProfile(
            provider_id="tensorrt-future",
            provider_type="tensorrt",
            endpoint_identity="local",
            health_status=CapabilityStatus.INTENTIONALLY_DISABLED,
            execution_backend="tensorrt",
        ),
    )


class LocalAIService:
    """Install-free capability, policy, registry, scheduling and provenance boundary."""

    def __init__(
        self,
        database_path: str | Path,
        *,
        probe: Probe | None = None,
        configuration: LocalAIConfiguration | None = None,
        hardware_registry: HardwareCapabilityRegistry | None = None,
        ollama_probe: OllamaProbe | None = None,
    ) -> None:
        self.database_path = Path(database_path)
        self.configuration = configuration or load_local_ai_configuration()
        self._hardware_registry = hardware_registry or HardwareCapabilityRegistry()
        self._ollama_probe = ollama_probe or _probe_ollama
        self._probe = probe or self._safe_probe

    def initialize(self) -> None:
        assert_schema_compatible(self.database_path)
        now = _now().isoformat()
        with self._connect() as connection:
            for provider in default_provider_profiles(self.configuration):
                if provider.provider_id == "ollama-local":
                    connection.execute(
                        "INSERT INTO local_ai_providers (provider_id, config_version, enabled, profile_json, created_at, updated_at) VALUES (?, 1, ?, ?, ?, ?) "
                        "ON CONFLICT(provider_id) DO UPDATE SET config_version = local_ai_providers.config_version + 1, enabled = excluded.enabled, profile_json = excluded.profile_json, updated_at = excluded.updated_at "
                        "WHERE local_ai_providers.enabled != excluded.enabled OR local_ai_providers.profile_json != excluded.profile_json",
                        (provider.provider_id, int(provider.enabled), provider.model_dump_json(), now, now),
                    )
                else:
                    connection.execute(
                        "INSERT OR IGNORE INTO local_ai_providers (provider_id, config_version, enabled, profile_json, created_at, updated_at) VALUES (?, 1, ?, ?, ?, ?)",
                        (provider.provider_id, int(provider.enabled), provider.model_dump_json(), now, now),
                    )
            for model in default_model_profiles(self.configuration):
                if model.model_profile_id == "configured-local-model":
                    connection.execute(
                        "INSERT INTO local_ai_models (model_profile_id, config_version, provider_id, enabled, profile_json, created_at, updated_at) VALUES (?, 1, ?, ?, ?, ?, ?) "
                        "ON CONFLICT(model_profile_id) DO UPDATE SET config_version = local_ai_models.config_version + 1, provider_id = excluded.provider_id, enabled = excluded.enabled, profile_json = excluded.profile_json, updated_at = excluded.updated_at "
                        "WHERE local_ai_models.provider_id != excluded.provider_id OR local_ai_models.enabled != excluded.enabled OR local_ai_models.profile_json != excluded.profile_json",
                        (model.model_profile_id, model.provider_id, int(model.enabled), model.model_dump_json(), now, now),
                    )
                else:
                    connection.execute(
                        "INSERT OR IGNORE INTO local_ai_models (model_profile_id, config_version, provider_id, enabled, profile_json, created_at, updated_at) VALUES (?, 1, ?, ?, ?, ?, ?)",
                        (model.model_profile_id, model.provider_id, int(model.enabled), model.model_dump_json(), now, now),
                    )

    def capability_report(self, *, refresh: bool = False, max_age_seconds: int = 60) -> HostCapabilityReport:
        if not refresh:
            cached = self._latest_snapshot(max_age_seconds)
            if cached is not None:
                return cached
        capabilities = self._probe()
        now = _now()
        report = HostCapabilityReport(
            report_id=f"cap-{uuid4().hex}",
            os_name=platform.platform(),
            architecture=platform.machine() or "unknown",
            python_version=platform.python_version(),
            wsl="microsoft" in platform.release().lower() or bool(os.environ.get("WSL_DISTRO_NAME")),
            capabilities=capabilities,
            disk=self._disk_report(),
            warnings=self._split_environment_warnings(capabilities),
            generated_at=now,
        )
        with self._connect() as connection:
            previous = connection.execute(
                "SELECT report_json FROM local_ai_capability_snapshots ORDER BY generated_at DESC LIMIT 1"
            ).fetchone()
            connection.execute(
                "INSERT INTO local_ai_capability_snapshots (snapshot_id, report_hash, report_json, generated_at) VALUES (?, ?, ?, ?)",
                (report.report_id, content_hash(report.model_dump(mode="json")), report.model_dump_json(), now.isoformat()),
            )
            if previous is not None and content_hash(self._material_capability_payload(
                HostCapabilityReport.model_validate_json(previous["report_json"])
            )) != content_hash(self._material_capability_payload(report)):
                self._audit(connection, "capability_change", report.report_id, {"material_change": True})
        return report

    def refresh_capabilities(self, *, actor_id: str, expected_snapshot_id: str | None,
                             idempotency_key: str) -> HostCapabilityReport:
        request_hash = content_hash({
            "actor_id": actor_id, "expected_snapshot_id": expected_snapshot_id,
            "operation": "refresh_capabilities",
        })
        with self._connect() as connection:
            replay = self._config_replay(connection, idempotency_key, request_hash)
            if replay is not None:
                return HostCapabilityReport.model_validate(replay)
            current = connection.execute(
                "SELECT snapshot_id FROM local_ai_capability_snapshots ORDER BY generated_at DESC LIMIT 1"
            ).fetchone()
            if expected_snapshot_id is not None and (
                current is None or current["snapshot_id"] != expected_snapshot_id
            ):
                raise ValueError("stale_capability_snapshot")
        report = self.capability_report(refresh=True)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._store_config_result(connection, idempotency_key, request_hash, report.model_dump(mode="json"))
            self._audit(connection, "capabilities_refreshed", report.report_id, {"actor_id": actor_id})
            connection.execute("COMMIT")
        return report

    def providers(self) -> tuple[ProviderProfile, ...]:
        with self._connect() as connection:
            rows = connection.execute("SELECT profile_json FROM local_ai_providers ORDER BY provider_id").fetchall()
        return tuple(ProviderProfile.model_validate_json(row["profile_json"]) for row in rows)

    def models(self) -> tuple[ModelProfile, ...]:
        with self._connect() as connection:
            rows = connection.execute("SELECT profile_json FROM local_ai_models ORDER BY model_profile_id").fetchall()
        return tuple(ModelProfile.model_validate_json(row["profile_json"]) for row in rows)

    def set_model_enabled(self, model_profile_id: str, *, enabled: bool, actor_id: str,
                          expected_version: int, idempotency_key: str) -> ModelProfile:
        request = {"model_profile_id": model_profile_id, "enabled": enabled, "actor_id": actor_id,
                   "expected_version": expected_version, "idempotency_key": idempotency_key}
        request_hash = content_hash(request)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            replay = self._config_replay(connection, idempotency_key, request_hash)
            if replay is not None:
                connection.execute("COMMIT")
                return ModelProfile.model_validate(replay)
            row = connection.execute(
                "SELECT config_version, profile_json FROM local_ai_models WHERE model_profile_id = ?",
                (model_profile_id,),
            ).fetchone()
            if row is None:
                raise ValueError("model profile not found")
            if int(row["config_version"]) != expected_version:
                raise ValueError("stale_configuration_version")
            profile = ModelProfile.model_validate_json(row["profile_json"])
            if enabled and not profile.local_available and profile.provider_id != "fake-deterministic":
                raise ValueError("model_not_locally_available")
            updated = profile.model_copy(update={"enabled": enabled})
            now = _now().isoformat()
            connection.execute(
                "UPDATE local_ai_models SET config_version = config_version + 1, enabled = ?, profile_json = ?, updated_at = ? WHERE model_profile_id = ? AND config_version = ?",
                (int(enabled), updated.model_dump_json(), now, model_profile_id, expected_version),
            )
            self._store_config_result(connection, idempotency_key, request_hash, updated.model_dump(mode="json"))
            self._audit(connection, "model_configuration_changed", model_profile_id, {"enabled": enabled, "actor_id": actor_id})
            connection.execute("COMMIT")
            return updated

    def set_role_mapping(self, role_id: str, model_profile_id: str, *, actor_id: str,
                         expected_version: int, idempotency_key: str) -> dict[str, object]:
        request = {"role_id": role_id, "model_profile_id": model_profile_id,
                   "actor_id": actor_id, "expected_version": expected_version}
        request_hash = content_hash(request)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            replay = self._config_replay(connection, idempotency_key, request_hash)
            if replay is not None:
                connection.execute("COMMIT")
                return replay
            model = connection.execute(
                "SELECT enabled FROM local_ai_models WHERE model_profile_id = ?", (model_profile_id,)
            ).fetchone()
            if model is None or not bool(model["enabled"]):
                raise ValueError("model_profile_not_enabled")
            existing = connection.execute(
                "SELECT config_version FROM local_ai_role_mappings WHERE role_id = ?", (role_id,)
            ).fetchone()
            current_version = int(existing["config_version"]) if existing else 0
            if current_version != expected_version:
                raise ValueError("stale_configuration_version")
            result = {"schema_version": "astra.local-ai.role-mapping.v1", "role_id": role_id,
                      "model_profile_id": model_profile_id, "configuration_version": current_version + 1}
            now = _now().isoformat()
            connection.execute(
                "INSERT INTO local_ai_role_mappings (role_id, model_profile_id, config_version, mapping_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(role_id) DO UPDATE SET model_profile_id = excluded.model_profile_id, config_version = excluded.config_version, mapping_json = excluded.mapping_json, updated_at = excluded.updated_at",
                (role_id, model_profile_id, current_version + 1, canonical_json(result), now, now),
            )
            self._store_config_result(connection, idempotency_key, request_hash, result)
            self._audit(connection, "role_mapping_changed", role_id, {"actor_id": actor_id, "model_profile_id": model_profile_id})
            connection.execute("COMMIT")
            return result

    def _durable_gpu_busy(self) -> bool:
        """Read the live scheduler state, not caller-supplied advisory input.

        GPU exclusivity is enforced durably at claim time (`claim_next`); this
        mirrors that same check so the advisory preview cannot recommend GPU
        admission while a GPU-exclusive job is already claimed/running.
        """
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT job_json FROM local_ai_scheduler_jobs WHERE status IN ('claimed', 'running')"
            ).fetchall()
        return any(
            SchedulerJob.model_validate_json(row["job_json"]).resource_request.gpu_exclusive
            for row in rows
        )

    def admission_preview(self, request: HardwareAdmissionRequest, *, report: HostCapabilityReport | None = None) -> HardwareAdmissionDecision:
        report = report or self.capability_report()
        vram = next((item for item in report.capabilities if item.capability_id == "vram"), None)
        memory = next((item for item in report.capabilities if item.capability_id == "memory"), None)
        provider = next(
            (item for item in report.capabilities if item.capability_id == "ollama"),
            None,
        )
        reserve = 768 * 1024**2
        configured_profile = next(
            (
                profile
                for profile in default_model_profiles(self.configuration)
                if profile.model_profile_id == request.model_profile_id
            ),
            None,
        )
        model_bytes = max(
            request.estimated_model_bytes,
            configured_profile.estimated_model_bytes if configured_profile else 0,
        )
        kv = request.requested_context * request.estimated_kv_bytes_per_token
        required = model_bytes + kv + 256 * 1024**2
        if (
            configured_profile is not None
            and provider is not None
            and provider.status != CapabilityStatus.AVAILABLE
        ):
            return self._decision(
                AdmissionOutcome.BLOCKED_PROVIDER,
                provider.reason or "The exact configured local model is unavailable.",
                required,
                None,
                reserve,
            )
        free_vram = getattr(vram, "free_bytes", None)
        total_vram = getattr(vram, "total_bytes", None)
        available_ram = getattr(memory, "available_bytes", None)
        if request.prefer_gpu and vram is not None and vram.status == CapabilityStatus.AVAILABLE:
            usable = max(0, int(free_vram or total_vram or 0) - reserve)
            if request.concurrent_gpu_jobs > 0 or self._durable_gpu_busy():
                return self._decision(AdmissionOutcome.QUEUED, "One GPU inference workload is allowed at a time.", required, usable, reserve)
            if required <= usable:
                return self._decision(AdmissionOutcome.GPU, "Estimated workload fits conservative GPU policy.", required, usable, reserve, "cuda", "gpu:0", request.requested_context)
            reduced = min(4096, request.requested_context)
            reduced_required = model_bytes + reduced * request.estimated_kv_bytes_per_token + 256 * 1024**2
            if reduced < request.requested_context and reduced_required <= usable:
                return self._decision(AdmissionOutcome.REDUCED_CONTEXT, "GPU admission requires a reduced operational context.", reduced_required, usable, reserve, "cuda", "gpu:0", reduced)
        if request.allow_cpu_fallback and self.configuration.allow_cpu_fallback:
            if available_ram is not None and required > int(available_ram):
                return self._decision(AdmissionOutcome.BLOCKED_RAM, "Estimated model and context exceed available system RAM.", required, int(available_ram), reserve)
            return self._decision(AdmissionOutcome.CPU, "GPU admission unavailable; explicit CPU fallback was permitted.", required, available_ram, reserve, "cpu", "cpu", request.requested_context)
        return self._decision(AdmissionOutcome.BLOCKED_VRAM, "Conservative GPU headroom is insufficient and CPU fallback was not authorized.", required, free_vram, reserve)

    def enqueue(self, *, workload_class: str, resource_request: ResourceRequest,
                admission: HardwareAdmissionDecision, idempotency_key: str,
                project_run_id: str | None = None, conversation_id: str | None = None,
                coordinator_intent_id: str | None = None, priority: int = 100) -> SchedulerJob:
        request = {"workload_class": workload_class, "resource_request": resource_request.model_dump(mode="json"),
                   "admission": admission.model_dump(mode="json"), "project_run_id": project_run_id,
                   "conversation_id": conversation_id, "coordinator_intent_id": coordinator_intent_id,
                   "priority": priority}
        request_hash = content_hash(request)
        now = _now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT request_hash, job_json FROM local_ai_scheduler_jobs WHERE idempotency_key = ?", (idempotency_key,)).fetchone()
            if row is not None:
                if row["request_hash"] != request_hash:
                    raise ValueError("idempotency_payload_mismatch")
                connection.execute("COMMIT")
                return SchedulerJob.model_validate_json(row["job_json"])
            position = int(connection.execute("SELECT COUNT(*) + 1 FROM local_ai_scheduler_jobs WHERE status = 'queued'").fetchone()[0])
            status = SchedulerStatus.QUEUED if admission.outcome not in {
                AdmissionOutcome.BLOCKED_VRAM, AdmissionOutcome.BLOCKED_RAM,
                AdmissionOutcome.BLOCKED_PROVIDER, AdmissionOutcome.BLOCKED_DEPENDENCY,
                AdmissionOutcome.BLOCKED_POLICY,
            } else SchedulerStatus.BLOCKED
            job = SchedulerJob(
                queue_id="local-ai", job_id=f"lai-{uuid4().hex}", project_run_id=project_run_id,
                conversation_id=conversation_id, coordinator_intent_id=coordinator_intent_id,
                workload_class=workload_class, resource_request=resource_request,
                admission=admission, status=status, idempotency_key=idempotency_key,
                request_hash=request_hash, priority=priority,
                queue_position=position if status == SchedulerStatus.QUEUED else None,
                created_at=now, updated_at=now,
            )
            connection.execute("INSERT INTO local_ai_scheduler_jobs (job_id, idempotency_key, request_hash, status, priority, job_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                               (job.job_id, idempotency_key, request_hash, status.value, priority, job.model_dump_json(), now.isoformat(), now.isoformat()))
            self._audit(connection, "scheduler_job_enqueued", job.job_id, {
                "status": status.value,
                "workload_class": workload_class,
                "request_hash": request_hash,
            })
            connection.execute("COMMIT")
            return job

    def scheduler_jobs(self) -> tuple[SchedulerJob, ...]:
        with self._connect() as connection:
            rows = connection.execute("SELECT job_json FROM local_ai_scheduler_jobs ORDER BY created_at, job_id").fetchall()
        return tuple(SchedulerJob.model_validate_json(row["job_json"]) for row in rows)

    def recover_expired_jobs(self) -> int:
        now = _now()
        recovered = 0
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT job_id, job_json FROM local_ai_scheduler_jobs WHERE status IN ('claimed', 'running')"
            ).fetchall()
            for row in rows:
                job = SchedulerJob.model_validate_json(row["job_json"])
                if job.lease_expires_at is None or job.lease_expires_at > now:
                    continue
                recovered_job = job.model_copy(update={
                    "status": SchedulerStatus.QUEUED,
                    "lease_owner": None,
                    "lease_expires_at": None,
                    "updated_at": now,
                    "started_at": None,
                })
                connection.execute(
                    "UPDATE local_ai_scheduler_jobs SET status = 'queued', job_json = ?, updated_at = ? WHERE job_id = ?",
                    (recovered_job.model_dump_json(), now.isoformat(), job.job_id),
                )
                self._audit(connection, "scheduler_lease_recovered", job.job_id, {
                    "previous_lease_owner": job.lease_owner,
                })
                recovered += 1
            connection.execute("COMMIT")
        return recovered

    def claim_next(self, *, worker_id: str, lease_seconds: int = 60,
                   cpu_limit: int = 2) -> SchedulerJob | None:
        now = _now()
        self.recover_expired_jobs()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            active_rows = connection.execute(
                "SELECT job_json FROM local_ai_scheduler_jobs WHERE status IN ('claimed', 'running')"
            ).fetchall()
            active = [SchedulerJob.model_validate_json(row["job_json"]) for row in active_rows]
            gpu_busy = any(item.resource_request.gpu_exclusive for item in active)
            cpu_active = sum(item.resource_request.cpu_slots for item in active)
            queued = connection.execute(
                "SELECT job_json FROM local_ai_scheduler_jobs WHERE status = 'queued' ORDER BY priority, created_at, job_id"
            ).fetchall()
            selected = None
            for row in queued:
                candidate = SchedulerJob.model_validate_json(row["job_json"])
                if candidate.resource_request.gpu_exclusive and gpu_busy:
                    continue
                if not candidate.resource_request.gpu_exclusive and cpu_active + candidate.resource_request.cpu_slots > cpu_limit:
                    continue
                selected = candidate
                break
            if selected is None:
                connection.execute("COMMIT")
                return None
            claimed = selected.model_copy(update={
                "status": SchedulerStatus.CLAIMED,
                "lease_owner": worker_id,
                "lease_expires_at": now + timedelta(seconds=lease_seconds),
                "updated_at": now,
                "started_at": now,
                "queue_position": None,
            })
            cursor = connection.execute(
                "UPDATE local_ai_scheduler_jobs SET status = 'claimed', job_json = ?, updated_at = ? WHERE job_id = ? AND status = 'queued'",
                (claimed.model_dump_json(), now.isoformat(), claimed.job_id),
            )
            if cursor.rowcount == 1:
                self._audit(connection, "scheduler_job_claimed", claimed.job_id, {
                    "worker_id": worker_id,
                    "lease_expires_at": claimed.lease_expires_at.isoformat(),
                })
            connection.execute("COMMIT")
            return claimed if cursor.rowcount == 1 else None

    def complete_job(self, job_id: str, *, worker_id: str, succeeded: bool,
                     error: RuntimeUnavailabilityReason | None = None) -> SchedulerJob:
        now = _now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT job_json FROM local_ai_scheduler_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise ValueError("scheduler_job_not_found")
            job = SchedulerJob.model_validate_json(row["job_json"])
            if job.status in {SchedulerStatus.COMPLETED, SchedulerStatus.FAILED}:
                connection.execute("COMMIT")
                return job
            if job.status != SchedulerStatus.CLAIMED or job.lease_owner != worker_id:
                raise ValueError("scheduler_lease_mismatch")
            completed = job.model_copy(update={
                "status": SchedulerStatus.COMPLETED if succeeded else SchedulerStatus.FAILED,
                "error": error,
                "updated_at": now,
                "completed_at": now,
                "lease_expires_at": None,
            })
            connection.execute(
                "UPDATE local_ai_scheduler_jobs SET status = ?, job_json = ?, updated_at = ? WHERE job_id = ?",
                (completed.status.value, completed.model_dump_json(), now.isoformat(), job_id),
            )
            self._audit(connection, "scheduler_job_completed" if succeeded else "scheduler_job_failed", job_id, {
                "worker_id": worker_id,
                "error_category": error.category if error is not None else None,
            })
            connection.execute("COMMIT")
            return completed

    def timeout_job(self, job_id: str, *, worker_id: str,
                    error: RuntimeUnavailabilityReason) -> SchedulerJob:
        """Record an observed execution timeout without granting retry authority."""
        now = _now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT job_json FROM local_ai_scheduler_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise ValueError("scheduler_job_not_found")
            job = SchedulerJob.model_validate_json(row["job_json"])
            if job.status == SchedulerStatus.TIMED_OUT:
                connection.execute("COMMIT")
                return job
            if job.status != SchedulerStatus.CLAIMED or job.lease_owner != worker_id:
                raise ValueError("scheduler_lease_mismatch")
            timed_out = job.model_copy(update={
                "status": SchedulerStatus.TIMED_OUT,
                "error": error,
                "updated_at": now,
                "completed_at": now,
                "lease_expires_at": None,
            })
            connection.execute(
                "UPDATE local_ai_scheduler_jobs SET status = 'timed_out', job_json = ?, updated_at = ? WHERE job_id = ?",
                (timed_out.model_dump_json(), now.isoformat(), job_id),
            )
            self._audit(connection, "scheduler_job_timed_out", job_id, {
                "worker_id": worker_id,
                "error_category": error.category,
            })
            connection.execute("COMMIT")
            return timed_out

    def cancel_job(self, job_id: str, *, actor_id: str, expected_status: SchedulerStatus,
                   idempotency_key: str) -> SchedulerJob:
        request_hash = content_hash({"job_id": job_id, "actor_id": actor_id, "expected_status": expected_status.value})
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            replay = connection.execute("SELECT request_hash, result_json FROM local_ai_config_idempotency WHERE idempotency_key = ?", (idempotency_key,)).fetchone()
            if replay is not None:
                if replay["request_hash"] != request_hash:
                    raise ValueError("idempotency_payload_mismatch")
                connection.execute("COMMIT")
                return SchedulerJob.model_validate_json(replay["result_json"])
            row = connection.execute("SELECT status, job_json FROM local_ai_scheduler_jobs WHERE job_id = ?", (job_id,)).fetchone()
            if row is None or row["status"] != expected_status.value:
                raise ValueError("stale_scheduler_binding")
            job = SchedulerJob.model_validate_json(row["job_json"]).model_copy(update={
                "status": SchedulerStatus.CANCELLED, "updated_at": _now(), "completed_at": _now(),
            })
            connection.execute("UPDATE local_ai_scheduler_jobs SET status = ?, job_json = ?, updated_at = ? WHERE job_id = ?",
                               (job.status.value, job.model_dump_json(), job.updated_at.isoformat(), job_id))
            self._store_config_result(connection, idempotency_key, request_hash, job.model_dump(mode="json"))
            self._audit(connection, "scheduler_job_cancelled", job_id, {"actor_id": actor_id})
            connection.execute("COMMIT")
            return job

    def store_provenance(self, record: ExecutionProvenance) -> ExecutionProvenance:
        material = record.model_dump(mode="json")
        # Hidden/provider reasoning is deliberately absent from the contract and
        # is additionally stripped from open configuration/diagnostic maps.
        for container in (material.get("configuration", {}), material.get("redacted_diagnostics", {})):
            for key in tuple(container):
                if "reasoning" in key.lower() or "chain_of_thought" in key.lower():
                    container.pop(key, None)
        clean = ExecutionProvenance.model_validate(material)
        with self._connect() as connection:
            connection.execute("INSERT OR IGNORE INTO local_ai_execution_provenance (execution_id, project_run_id, provider_id, model_profile_id, status, provenance_json, created_at, completed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                               (clean.execution_id, clean.project_run_id, clean.provider_id, clean.model_profile_id,
                                "completed" if clean.completed_at else "running", clean.model_dump_json(), clean.started_at.isoformat(),
                                clean.completed_at.isoformat() if clean.completed_at else None))
        return clean

    def provenance(self, limit: int = 100) -> tuple[ExecutionProvenance, ...]:
        with self._connect() as connection:
            rows = connection.execute("SELECT provenance_json FROM local_ai_execution_provenance ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return tuple(ExecutionProvenance.model_validate_json(row["provenance_json"]) for row in rows)

    @staticmethod
    def disabled_status() -> dict[str, object]:
        return {
            "schema_version": "astra.local-ai.future-status.v1",
            "project_rag": {"enabled": False, "status": "intentionally_disabled", "sources": {
                "cline-main.zip": "awaiting_validation", "continue-main.zip": "awaiting_validation",
                "aider-main.zip": "awaiting_validation", "2024-main.zip": "excluded_pending_license",
                "astra_corpus/train.csv": "not_ingested",
            }, "exclusions": (
                "binary_models", "onnx", "media", "dependencies", "vendor_trees",
                "generated_assets", "archives", "executables", "dlls", "installers",
                "game_mods", "credentials", "secrets",
            ), "retrieved_content_authority": "untrusted_evidence_only"},
            "evaluation": {"enabled": False, "status": "definitions_only"},
            "training": {"enabled": False, "status": "blocked_by_policy"},
        }

    def _safe_probe(self) -> tuple[Capability, ...]:
        now = _now()
        snapshot = self._hardware_registry.snapshot(refresh=True)
        reachable, installed_models, loaded_models, provider_reason = self._ollama_probe(
            self.configuration
        )
        configured_missing = reachable and any(
            model not in installed_models for model in self.configuration.configured_models
        )
        ollama_status = (
            CapabilityStatus.UNAVAILABLE
            if not reachable or configured_missing
            else CapabilityStatus.AVAILABLE
        )
        import importlib.util

        onnx_installed = importlib.util.find_spec("onnxruntime") is not None
        tensorrt_installed = importlib.util.find_spec("tensorrt") is not None
        device_names = tuple(item.name or "unknown" for item in snapshot.gpu_devices)
        device_identities = tuple(
            item.stable_identity or f"gpu:{item.index}" for item in snapshot.gpu_devices
        )
        return (
            CPUCapability(
                capability_id="cpu", status=CapabilityStatus.AVAILABLE,
                model=snapshot.cpu_name, architecture=snapshot.architecture,
                logical_cores=snapshot.cpu_logical_cores or 1,
                physical_cores=snapshot.cpu_physical_cores, probed_at=now,
                provenance={"registry": snapshot.schema_version, "sources": snapshot.probe_sources},
            ),
            MemoryCapability(
                capability_id="memory",
                status=CapabilityStatus.AVAILABLE if snapshot.total_memory_bytes else CapabilityStatus.UNKNOWN,
                total_bytes=snapshot.total_memory_bytes,
                available_bytes=snapshot.available_memory_bytes,
                estimate=snapshot.available_memory_estimate, probed_at=now,
                provenance={
                    **snapshot.memory_provenance,
                    "registry": snapshot.schema_version,
                    "sources": snapshot.probe_sources,
                },
            ),
            PyTorchCapability(
                capability_id="pytorch",
                status=CapabilityStatus.AVAILABLE if snapshot.pytorch_installed else CapabilityStatus.UNAVAILABLE,
                installed=bool(snapshot.pytorch_installed),
                cuda_available=bool(snapshot.cuda_available),
                cuda_version=snapshot.runtime_cuda_version,
                device_count=len(snapshot.gpu_devices), version=snapshot.pytorch_version,
                probed_at=now, provenance={"registry": snapshot.schema_version},
            ),
            CUDACapability(
                capability_id="cuda",
                status=CapabilityStatus.AVAILABLE if snapshot.cuda_available else CapabilityStatus.UNAVAILABLE,
                driver_visible=bool(snapshot.driver_version or snapshot.cuda_available),
                runtime_visible=bool(snapshot.runtime_cuda_version),
                runtime_version=snapshot.runtime_cuda_version, probed_at=now,
                provenance={"registry": snapshot.schema_version},
            ),
            GPUCapability(
                capability_id="gpu",
                status=CapabilityStatus.AVAILABLE if snapshot.gpu_devices else CapabilityStatus.UNAVAILABLE,
                device_count=len(snapshot.gpu_devices), device_names=device_names,
                device_identities=device_identities,
                devices=tuple(item.model_dump(mode="json") for item in snapshot.gpu_devices),
                driver_version=snapshot.driver_version, probed_at=now,
                provenance={"registry": snapshot.schema_version, "errors": snapshot.errors},
            ),
            VRAMCapability(
                capability_id="vram",
                status=CapabilityStatus.AVAILABLE if snapshot.total_vram_bytes is not None else CapabilityStatus.UNAVAILABLE,
                total_bytes=max(
                    (
                        item.total_vram_bytes
                        for item in snapshot.gpu_devices
                        if item.total_vram_bytes is not None
                    ),
                    default=None,
                ),
                free_bytes=max(
                    (
                        item.free_vram_bytes
                        for item in snapshot.gpu_devices
                        if item.free_vram_bytes is not None
                    ),
                    default=None,
                ),
                estimate=not any(
                    item.free_vram_bytes is not None for item in snapshot.gpu_devices
                ),
                probed_at=now,
                provenance={
                    "registry": snapshot.schema_version,
                    "interpretation": "largest_single_device",
                },
            ),
            OllamaCapability(
                capability_id="ollama", status=ollama_status,
                endpoint=self.configuration.endpoint_identity,
                configured_models=self.configuration.configured_models,
                installed_models=installed_models, loaded_models=loaded_models,
                provider_reachable=reachable,
                configured_model_missing=configured_missing,
                probed_at=now, reason=provider_reason,
                provenance={
                    "configuration_authority": self.configuration.schema_version,
                    "network_probe": True, "no_auto_start": True, "no_auto_pull": True,
                },
            ),
            ONNXRuntimeCapability(capability_id="onnxruntime", status=CapabilityStatus.AVAILABLE if onnx_installed else CapabilityStatus.UNAVAILABLE,
                                  installed=onnx_installed, probed_at=now, provenance={"probe": "importlib"}),
            TensorRTCapability(capability_id="tensorrt", status=CapabilityStatus.AVAILABLE if tensorrt_installed else CapabilityStatus.UNAVAILABLE,
                               installed=tensorrt_installed, probed_at=now, provenance={"probe": "importlib"}),
            TrainingCapability(capability_id="training", status=CapabilityStatus.INTENTIONALLY_DISABLED,
                               enabled=False, probed_at=now, reason="Training is disabled by policy.", provenance={"policy": "stage7a"}),
        )

    def _latest_snapshot(self, max_age_seconds: int) -> HostCapabilityReport | None:
        with self._connect() as connection:
            row = connection.execute("SELECT report_json, generated_at FROM local_ai_capability_snapshots ORDER BY generated_at DESC LIMIT 1").fetchone()
        if row is None or datetime.fromisoformat(row["generated_at"]) < _now() - timedelta(seconds=max_age_seconds):
            return None
        return HostCapabilityReport.model_validate_json(row["report_json"])

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    @staticmethod
    def _decision(outcome: AdmissionOutcome, reason: str, required: int, available: int | None,
                  reserve: int, backend: str | None = None, device: str | None = None,
                  context: int | None = None) -> HardwareAdmissionDecision:
        return HardwareAdmissionDecision(outcome=outcome, reason=reason, backend=backend, device=device,
                                         admitted_context=context, estimated_required_bytes=required,
                                         available_bytes=available, safety_reserve_bytes=reserve,
                                         requires_explicit_approval=outcome in {AdmissionOutcome.CPU, AdmissionOutcome.REDUCED_CONTEXT})

    @staticmethod
    def _disk_report() -> dict[str, object]:
        usage = shutil.disk_usage(Path.cwd())
        return {"path_kind": "workspace", "total_bytes": usage.total, "free_bytes": usage.free}

    @staticmethod
    def _split_environment_warnings(capabilities: tuple[Capability, ...]) -> tuple[str, ...]:
        if os.environ.get("WSL_DISTRO_NAME") and shutil.which("ollama") is None:
            return ("WSL is active but Ollama is not visible inside WSL; verify whether Ollama runs on Windows and configure the endpoint explicitly.",)
        return ()

    @staticmethod
    def _material_capability_payload(report: HostCapabilityReport) -> dict[str, object]:
        """Remove identity/timestamp noise before classifying a probe as changed."""
        capabilities = []
        for capability in report.capabilities:
            value = capability.model_dump(mode="json")
            value.pop("probed_at", None)
            capabilities.append(value)
        return {
            "os_name": report.os_name,
            "architecture": report.architecture,
            "python_version": report.python_version,
            "wsl": report.wsl,
            "capabilities": capabilities,
            "disk": report.disk,
            "warnings": report.warnings,
        }

    @staticmethod
    def _audit(connection: sqlite3.Connection, event_type: str, aggregate_id: str, payload: dict[str, object]) -> None:
        now = _now().isoformat()
        connection.execute("INSERT INTO local_ai_audit_events (event_id, event_type, aggregate_id, event_json, created_at) VALUES (?, ?, ?, ?, ?)",
                           (uuid4().hex, event_type, aggregate_id, canonical_json(payload), now))

    @staticmethod
    def _config_replay(connection: sqlite3.Connection, key: str, request_hash: str) -> dict | None:
        row = connection.execute("SELECT request_hash, result_json FROM local_ai_config_idempotency WHERE idempotency_key = ?", (key,)).fetchone()
        if row is None:
            return None
        if row["request_hash"] != request_hash:
            raise ValueError("idempotency_payload_mismatch")
        return json.loads(row["result_json"])

    @staticmethod
    def _store_config_result(connection: sqlite3.Connection, key: str, request_hash: str, result: dict) -> None:
        connection.execute("INSERT INTO local_ai_config_idempotency (idempotency_key, request_hash, result_json, created_at) VALUES (?, ?, ?, ?)",
                           (key, request_hash, canonical_json(result), _now().isoformat()))


__all__ = ["LocalAIService", "default_model_profiles", "default_provider_profiles"]
