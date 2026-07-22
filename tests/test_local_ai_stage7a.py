from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.database.migrations import apply_schema_migrations
from backend.app.local_ai.contracts import (
    AdmissionOutcome,
    CapabilityStatus,
    CPUCapability,
    ExecutionProvenance,
    HardwareAdmissionRequest,
    MemoryCapability,
    ResourceRequest,
    RuntimeUnavailabilityReason,
    SchedulerStatus,
    VRAMCapability,
)
from backend.app.local_ai.service import LocalAIService, _parse_linux_meminfo, _probe_host_memory
from backend.app.main import create_app


def _capabilities(*, vram: int | None, free_vram: int | None, ram: int = 16 * 1024**3):
    now = datetime.now(timezone.utc)
    return (
        CPUCapability(capability_id="cpu", status=CapabilityStatus.AVAILABLE,
                      architecture="x86_64", logical_cores=8, probed_at=now),
        MemoryCapability(capability_id="memory", status=CapabilityStatus.AVAILABLE,
                         total_bytes=ram, available_bytes=ram // 2, probed_at=now),
        VRAMCapability(capability_id="vram", status=CapabilityStatus.AVAILABLE if vram else CapabilityStatus.UNAVAILABLE,
                       total_bytes=vram, free_bytes=free_vram, estimate=False, probed_at=now),
    )


def _service(tmp_path: Path, probe):
    database = tmp_path / "stage7a.db"
    apply_schema_migrations(database)
    service = LocalAIService(database, probe=probe)
    service.initialize()
    return service


def test_linux_meminfo_uses_memavailable_exactly(tmp_path: Path) -> None:
    meminfo = tmp_path / "meminfo"
    meminfo.write_text(
        "MemTotal:       16384000 kB\n"
        "MemFree:          156000 kB\n"
        "MemAvailable:    6710886 kB\n"
        "Buffers:          120000 kB\n"
        "Cached:          2400000 kB\n",
        encoding="ascii",
    )
    result = _probe_host_memory(system="Linux", meminfo_path=meminfo)
    assert result.total_bytes == 16384000 * 1024
    assert result.available_bytes == 6710886 * 1024
    assert result.estimate is False
    assert result.provenance["available_source"] == "MemAvailable"


def test_linux_meminfo_without_memavailable_uses_marked_conservative_estimate() -> None:
    result = _parse_linux_meminfo(
        "MemTotal:       8388608 kB\n"
        "MemFree:         100000 kB\n"
        "Buffers:          20000 kB\n"
        "Cached:          600000 kB\n"
        "SReclaimable:    100000 kB\n"
        "Shmem:            50000 kB\n"
    )
    assert result.total_bytes == 8388608 * 1024
    assert result.available_bytes == (100000 + 20000 + 600000 + 100000 - 50000) * 1024
    assert result.estimate is True
    assert result.provenance["estimate"] is True


def test_doctor_and_capabilities_expose_memavailable(monkeypatch, tmp_path: Path) -> None:
    total = 16 * 1024**3
    available = 6400 * 1024**2

    def probe(_self):
        now = datetime.now(timezone.utc)
        return (
            MemoryCapability(
                capability_id="memory", status=CapabilityStatus.AVAILABLE,
                total_bytes=total, available_bytes=available, estimate=False,
                probed_at=now, provenance={"probe": "proc_meminfo", "available_source": "MemAvailable"},
            ),
        )

    monkeypatch.setattr(LocalAIService, "_safe_probe", probe)
    with TestClient(create_app(tmp_path / "memory-api.db", tmp_path)) as client:
        for endpoint in ("/runtime/local-ai/doctor", "/runtime/local-ai/capabilities"):
            response = client.get(endpoint)
            assert response.status_code == 200
            memory = next(item for item in response.json()["capabilities"] if item["capability_id"] == "memory")
            assert memory["total_bytes"] == total
            assert memory["available_bytes"] == available
            assert memory["estimate"] is False
            assert memory["provenance"]["available_source"] == "MemAvailable"


def test_capability_registry_caches_and_explicitly_refreshes(tmp_path: Path) -> None:
    calls = 0

    def probe():
        nonlocal calls
        calls += 1
        return _capabilities(vram=None, free_vram=None)

    service = _service(tmp_path, probe)
    first = service.capability_report()
    assert service.capability_report().report_id == first.report_id
    assert calls == 1
    assert service.capability_report(refresh=True).report_id != first.report_id
    assert calls == 2
    with service._connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM local_ai_audit_events WHERE event_type = 'capability_change'"
        ).fetchone()[0] == 0


def test_rtx_3050_policy_reduces_context_conservatively(tmp_path: Path) -> None:
    gib = 1024**3
    service = _service(tmp_path, lambda: _capabilities(vram=4 * gib, free_vram=4 * gib))
    decision = service.admission_preview(HardwareAdmissionRequest(
        workload_class="synthesis", model_profile_id="qwen3-4b-q4-k-m",
        estimated_model_bytes=2800 * 1024**2, requested_context=8192,
        estimated_kv_bytes_per_token=64 * 1024,
    ))
    assert decision.outcome == AdmissionOutcome.REDUCED_CONTEXT
    assert decision.admitted_context == 4096
    assert decision.requires_explicit_approval is True


def test_cpu_fallback_never_happens_silently(tmp_path: Path) -> None:
    gib = 1024**3
    service = _service(tmp_path, lambda: _capabilities(vram=None, free_vram=None))
    request = HardwareAdmissionRequest(
        workload_class="chat", model_profile_id="qwen3-4b-q4-k-m",
        estimated_model_bytes=3 * gib, requested_context=4096,
    )
    assert service.admission_preview(request).outcome == AdmissionOutcome.BLOCKED_VRAM
    assert service.admission_preview(request.model_copy(update={"allow_cpu_fallback": True})).outcome == AdmissionOutcome.CPU


def test_configured_profile_does_not_assume_model_is_installed(tmp_path: Path) -> None:
    service = _service(tmp_path, lambda: _capabilities(vram=None, free_vram=None))
    configured = next(
        item for item in service.models()
        if item.model_profile_id == "configured-local-model"
    )
    assert configured.provider_model_id == "qwen2.5-coder:1.5b"
    assert configured.local_available is False
    assert configured.enabled is False
    assert configured.source_metadata["no_auto_start"] is True
    assert configured.source_metadata["no_auto_pull"] is True


def test_future_rag_evaluation_and_training_are_disabled(tmp_path: Path) -> None:
    status = _service(tmp_path, lambda: _capabilities(vram=None, free_vram=None)).disabled_status()
    assert status["project_rag"]["enabled"] is False
    assert status["training"]["enabled"] is False
    assert status["project_rag"]["sources"]["2024-main.zip"] == "excluded_pending_license"
    assert status["project_rag"]["sources"]["astra_corpus/train.csv"] == "not_ingested"


def test_local_ai_read_apis_are_reload_safe(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path / "api.db", tmp_path)) as client:
        first = client.get("/runtime/local-ai/capabilities")
        assert first.status_code == 200
        second = client.get("/runtime/local-ai/capabilities")
        assert second.json()["report_id"] == first.json()["report_id"]
        future = client.get("/runtime/local-ai/future-status").json()
        assert future["project_rag"]["enabled"] is False
        assert future["training"]["enabled"] is False
        assert client.get("/runtime/local-ai/providers").status_code == 200
        assert client.get("/runtime/local-ai/models").status_code == 200
        assert client.get("/runtime/local-ai/scheduler").json()["items"] == []


def test_scheduler_deduplicates_and_enforces_one_gpu_claim(tmp_path: Path) -> None:
    gib = 1024**3
    service = _service(tmp_path, lambda: _capabilities(vram=4 * gib, free_vram=4 * gib))
    admission = service.admission_preview(HardwareAdmissionRequest(
        workload_class="chat", model_profile_id="fake-deterministic",
        estimated_model_bytes=0, requested_context=1024,
    ))
    resource = ResourceRequest(backend="cuda", gpu_exclusive=True)
    first = service.enqueue(workload_class="chat", resource_request=resource,
                            admission=admission, idempotency_key="job-one")
    replay = service.enqueue(workload_class="chat", resource_request=resource,
                             admission=admission, idempotency_key="job-one")
    assert replay.job_id == first.job_id
    service.enqueue(workload_class="synthesis", resource_request=resource,
                    admission=admission, idempotency_key="job-two")
    claimed = service.claim_next(worker_id="worker-1")
    assert claimed and claimed.status == SchedulerStatus.CLAIMED
    assert service.claim_next(worker_id="worker-2") is None
    assert service.complete_job(claimed.job_id, worker_id="worker-1", succeeded=True).status == SchedulerStatus.COMPLETED
    assert service.claim_next(worker_id="worker-2") is not None


def test_execution_provenance_discards_reasoning_content(tmp_path: Path) -> None:
    service = _service(tmp_path, lambda: _capabilities(vram=None, free_vram=None))
    admission = service.admission_preview(HardwareAdmissionRequest(
        workload_class="diagnosis", model_profile_id="fake-deterministic",
        estimated_model_bytes=0, requested_context=1024, allow_cpu_fallback=True,
    ))
    now = datetime.now(timezone.utc)
    stored = service.store_provenance(ExecutionProvenance(
        execution_id="execution-one", provider_id="fake-deterministic",
        model_profile_id="fake-deterministic", provider_model_id="fake:deterministic",
        prompt_template_version="fake-v1", configuration={"temperature": 0, "reasoning_text": "private"},
        context_limit=1024, output_limit=256, thinking_mode=True, backend="cpu", device="cpu",
        admission=admission, started_at=now, completed_at=now, duration_ms=1,
        response_hash="a" * 64, retry_identity="retry-one",
        redacted_diagnostics={"chain_of_thought": "private", "status": "ok"},
    ))
    assert "reasoning_text" not in stored.configuration
    assert "chain_of_thought" not in stored.redacted_diagnostics
    assert stored.reasoning_content_persisted is False


def test_scheduler_recovers_expired_lease_after_service_restart(tmp_path: Path) -> None:
    service = _service(tmp_path, lambda: _capabilities(vram=None, free_vram=None))
    admission = service.admission_preview(HardwareAdmissionRequest(
        workload_class="chat", model_profile_id="fake-deterministic",
        estimated_model_bytes=0, requested_context=1024, allow_cpu_fallback=True,
    ))
    job = service.enqueue(
        workload_class="chat", resource_request=ResourceRequest(backend="cpu"),
        admission=admission, idempotency_key="restart-job",
    )
    claimed = service.claim_next(worker_id="departed-worker", lease_seconds=-1)
    assert claimed and claimed.job_id == job.job_id

    restarted = LocalAIService(service.database_path, probe=lambda: _capabilities(vram=None, free_vram=None))
    restarted.initialize()
    assert restarted.recover_expired_jobs() == 1
    replayed = restarted.claim_next(worker_id="replacement-worker")
    assert replayed and replayed.job_id == job.job_id


def test_scheduler_timeout_is_terminal_and_audited(tmp_path: Path) -> None:
    service = _service(tmp_path, lambda: _capabilities(vram=None, free_vram=None))
    admission = service.admission_preview(HardwareAdmissionRequest(
        workload_class="chat", model_profile_id="fake-deterministic",
        estimated_model_bytes=0, requested_context=1024, allow_cpu_fallback=True,
    ))
    service.enqueue(
        workload_class="chat", resource_request=ResourceRequest(backend="cpu"),
        admission=admission, idempotency_key="timeout-job",
    )
    claimed = service.claim_next(worker_id="timeout-worker")
    assert claimed is not None
    reason = RuntimeUnavailabilityReason(
        category=CapabilityStatus.UNAVAILABLE, component="scheduler",
        message="The bounded execution timed out.",
    )
    timed_out = service.timeout_job(claimed.job_id, worker_id="timeout-worker", error=reason)
    assert timed_out.status == SchedulerStatus.TIMED_OUT
    assert service.timeout_job(claimed.job_id, worker_id="timeout-worker", error=reason).job_id == claimed.job_id
    with service._connect() as connection:
        event_types = {row[0] for row in connection.execute(
            "SELECT event_type FROM local_ai_audit_events WHERE aggregate_id = ?", (claimed.job_id,)
        )}
    assert {"scheduler_job_enqueued", "scheduler_job_claimed", "scheduler_job_timed_out"} <= event_types


def test_admission_preview_checks_durable_gpu_state_not_only_caller_input(tmp_path: Path) -> None:
    """Regression: a caller cannot get a misleading GPU admission decision by
    simply omitting/zeroing concurrent_gpu_jobs while a GPU-exclusive job is
    already claimed. The preview must consult durable scheduler state, matching
    the enforcement already applied at claim_next."""
    gib = 1024**3
    service = _service(tmp_path, lambda: _capabilities(vram=4 * gib, free_vram=4 * gib))
    request = HardwareAdmissionRequest(
        workload_class="chat", model_profile_id="fake-deterministic",
        estimated_model_bytes=0, requested_context=1024,
    )
    admission = service.admission_preview(request)
    assert admission.outcome == AdmissionOutcome.GPU

    resource = ResourceRequest(backend="cuda", gpu_exclusive=True)
    enqueued = service.enqueue(
        workload_class="chat", resource_request=resource,
        admission=admission, idempotency_key="gpu-holder",
    )
    claimed = service.claim_next(worker_id="worker-1")
    assert claimed is not None and claimed.job_id == enqueued.job_id

    # concurrent_gpu_jobs left at its default (0) — the caller did not self-report
    # a running job — but the scheduler already has one claimed.
    stale_preview = service.admission_preview(request)
    assert stale_preview.outcome == AdmissionOutcome.QUEUED
