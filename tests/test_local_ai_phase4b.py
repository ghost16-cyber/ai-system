from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.database.migrations import apply_schema_migrations
from backend.app.hardware_ai_optimizer.hardware_probe import snapshot_to_hardware_report
from backend.app.local_ai.config import (
    LocalAIConfigurationError,
    load_local_ai_configuration,
)
from backend.app.local_ai.contracts import (
    AdmissionOutcome,
    HardwareAdmissionRequest,
    OllamaCapability,
)
from backend.app.local_ai.hardware import (
    HardwareCapabilityRegistry,
    HardwareSnapshot,
    collect_hardware_snapshot,
)
from backend.app.local_ai.service import LocalAIService
from backend.app.main import create_app
from backend.app.slm.gateway import _model_available
from backend.app.project_control import (
    ProjectCommand,
    ProjectCommandType,
    ProjectControlPlane,
)
from backend.app.project_models import (
    ProjectModelInvocationStore,
    build_project_model_invocation,
)


class _UnavailableCuda:
    @staticmethod
    def is_available() -> bool:
        return False


class _UnavailableTorch:
    __version__ = "test"
    cuda = _UnavailableCuda()
    version = type("Version", (), {"cuda": None})()


def _snapshot(**updates) -> HardwareSnapshot:
    values = {
        "collected_at": datetime.now(timezone.utc),
        "platform": "Linux-test",
        "operating_system": "Linux",
        "architecture": "x86_64",
        "cpu_name": "Test CPU",
        "cpu_logical_cores": 8,
        "cpu_physical_cores": 4,
        "total_memory_bytes": 16 * 1024**3,
        "available_memory_bytes": 8 * 1024**3,
        "memory_provenance": {
            "probe": "proc_meminfo",
            "available_source": "MemAvailable",
        },
        "cuda_available": False,
        "pytorch_installed": False,
        "probe_sources": ("proc_meminfo",),
    }
    values.update(updates)
    return HardwareSnapshot(**values)


def _service(tmp_path: Path, *, configuration=None, ollama_probe=None) -> LocalAIService:
    database = tmp_path / "phase4b.db"
    apply_schema_migrations(database)
    registry = HardwareCapabilityRegistry(lambda: _snapshot(), ttl_seconds=5)
    service = LocalAIService(
        database,
        configuration=configuration,
        hardware_registry=registry,
        ollama_probe=ollama_probe,
    )
    service.initialize()
    return service


def test_default_configuration_uses_only_approved_installed_model() -> None:
    configuration = load_local_ai_configuration({})
    assert configuration.provider_type == "ollama"
    assert configuration.endpoint_identity == "http://127.0.0.1:11434"
    assert configuration.configured_models == ("qwen2.5-coder:1.5b",)
    assert configuration.model_for_role("planner") == "qwen2.5-coder:1.5b"


def test_configuration_environment_override_and_legacy_precedence() -> None:
    configuration = load_local_ai_configuration(
        {
            "ASTRA_OLLAMA_BASE_URL": "http://localhost:11500/",
            "ASTRA_SLM_MODEL": "legacy:1b",
            "ASTRA_LOCAL_AI_SYNTHESIS_MODEL": "synthesis:2b",
            "ASTRA_LOCAL_AI_REVIEWER_MODEL": "unset",
            "ASTRA_LOCAL_AI_CONNECTION_TIMEOUT_SECONDS": "3",
            "ASTRA_LOCAL_AI_GENERATION_TIMEOUT_SECONDS": "90",
            "ASTRA_LOCAL_AI_MAX_CONTEXT_TOKENS": "8192",
            "ASTRA_LOCAL_AI_MAX_OUTPUT_TOKENS": "1024",
            "ASTRA_LOCAL_AI_ALLOW_CPU_FALLBACK": "false",
        }
    )
    assert configuration.endpoint_identity == "http://localhost:11500"
    assert configuration.coder_model == "legacy:1b"
    assert configuration.synthesis_model == "synthesis:2b"
    assert configuration.planner_model == "legacy:1b"
    assert configuration.reviewer_model is None
    assert configuration.allow_cpu_fallback is False


@pytest.mark.parametrize(
    "environment,code",
    [
        ({"ASTRA_OLLAMA_ENDPOINT": "file:///tmp/ollama"}, "invalid_ollama_endpoint"),
        ({"ASTRA_OLLAMA_ENDPOINT": "http://user:secret@localhost"}, "invalid_ollama_endpoint"),
        ({"ASTRA_LOCAL_AI_MODEL": ""}, "astra_local_ai_model_is_empty"),
        ({"ASTRA_LOCAL_AI_MODEL": "bad model"}, "invalid_synthesis_model_tag"),
    ],
)
def test_invalid_local_ai_configuration_fails_closed(environment, code) -> None:
    with pytest.raises(LocalAIConfigurationError, match=code):
        load_local_ai_configuration(environment)


@pytest.mark.parametrize(
    "reachable,installed,loaded,status,missing",
    [
        (False, (), (), "unavailable", False),
        (True, ("another:1b",), (), "unavailable", True),
        (
            True,
            ("qwen2.5-coder:1.5b",),
            ("qwen2.5-coder:1.5b",),
            "available",
            False,
        ),
    ],
)
def test_provider_capability_distinguishes_reachability_installation_and_loading(
    tmp_path: Path, reachable, installed, loaded, status, missing
) -> None:
    calls = 0

    def probe(_configuration):
        nonlocal calls
        calls += 1
        return reachable, installed, loaded, None

    service = _service(tmp_path, ollama_probe=probe)
    report = service.capability_report(refresh=True)
    capability = next(
        item for item in report.capabilities if isinstance(item, OllamaCapability)
    )
    assert capability.status.value == status
    assert capability.configured_models == ("qwen2.5-coder:1.5b",)
    assert capability.installed_models == installed
    assert capability.loaded_models == loaded
    assert capability.provider_reachable is reachable
    assert capability.configured_model_missing is missing
    assert calls == 1
    assert capability.provenance["no_auto_start"] is True
    assert capability.provenance["no_auto_pull"] is True


def test_model_availability_requires_exact_tag_without_silent_substitution() -> None:
    assert _model_available("coder:1", ["coder:1"]) is True
    assert _model_available("coder", ["coder:latest"]) is False
    assert _model_available("coder:latest", ["coder"]) is False


def test_capability_probe_never_starts_or_pulls_ollama(monkeypatch, tmp_path: Path) -> None:
    attempted_commands: list[object] = []

    def forbidden(*args, **kwargs):
        attempted_commands.append((args, kwargs))
        raise AssertionError("local-AI capability probing must not launch commands")

    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    service = _service(
        tmp_path,
        ollama_probe=lambda _configuration: (False, (), (), "provider_unreachable"),
    )
    service.capability_report(refresh=True)
    assert attempted_commands == []


def test_doctor_and_capabilities_expose_exact_model_state(monkeypatch, tmp_path: Path) -> None:
    def probe(_configuration):
        return (
            True,
            ("qwen2.5-coder:1.5b",),
            ("qwen2.5-coder:1.5b",),
            None,
        )

    monkeypatch.setattr("backend.app.local_ai.service._probe_ollama", probe)
    with TestClient(create_app(tmp_path / "api.db", tmp_path)) as client:
        for endpoint in ("/runtime/local-ai/doctor", "/runtime/local-ai/capabilities"):
            response = client.get(endpoint)
            assert response.status_code == 200
            capability = next(
                item
                for item in response.json()["capabilities"]
                if item["capability_id"] == "ollama"
            )
            assert capability["configured_models"] == ["qwen2.5-coder:1.5b"]
            assert capability["installed_models"] == ["qwen2.5-coder:1.5b"]
            assert capability["loaded_models"] == ["qwen2.5-coder:1.5b"]
            assert capability["provider_reachable"] is True


def test_cpu_fallback_configuration_can_deny_explicit_request(tmp_path: Path) -> None:
    configuration = load_local_ai_configuration(
        {"ASTRA_LOCAL_AI_ALLOW_CPU_FALLBACK": "false"}
    )
    service = _service(
        tmp_path,
        configuration=configuration,
        ollama_probe=lambda _configuration: (False, (), (), "provider_unreachable"),
    )
    decision = service.admission_preview(
        HardwareAdmissionRequest(
            workload_class="synthesis",
            model_profile_id="configured-local-model",
            estimated_model_bytes=1,
            requested_context=1024,
            allow_cpu_fallback=True,
        ),
        report=service.capability_report(refresh=True),
    )
    assert decision.outcome == AdmissionOutcome.BLOCKED_PROVIDER


def test_hardware_probe_parses_multiple_gpus_and_physical_cores(tmp_path: Path) -> None:
    meminfo = tmp_path / "meminfo"
    meminfo.write_text(
        "MemTotal: 16000000 kB\nMemAvailable: 6400000 kB\n", encoding="ascii"
    )
    cpuinfo = tmp_path / "cpuinfo"
    cpuinfo.write_text(
        "model name: Test CPU\nphysical id: 0\ncore id: 0\n\n"
        "model name: Test CPU\nphysical id: 0\ncore id: 1\n\n",
        encoding="ascii",
    )

    def runner(command, timeout):
        assert command[0] == "nvidia-smi"
        assert timeout == 2.0
        return subprocess.CompletedProcess(
            command,
            0,
            "0, GPU-a, RTX A, 4096, 1024, 3072, 12, 555.1\n"
            "1, GPU-b, RTX B, 8192, 2048, 6144, 23, 555.1\n",
            "",
        )

    snapshot = collect_hardware_snapshot(
        system="Linux",
        meminfo_path=meminfo,
        cpuinfo_path=cpuinfo,
        torch_module=_UnavailableTorch,
        command_runner=runner,
    )
    assert snapshot.available_memory_bytes == 6400000 * 1024
    assert snapshot.available_memory_estimate is False
    assert snapshot.cpu_physical_cores == 2
    assert snapshot.cpu_logical_cores is not None
    assert [item.stable_identity for item in snapshot.gpu_devices] == ["GPU-a", "GPU-b"]
    assert snapshot.total_vram_bytes == 12 * 1024**3
    assert snapshot.free_vram_bytes == 9 * 1024**3
    assert snapshot.driver_version == "555.1"


@pytest.mark.parametrize(
    "runner,error_code",
    [
        (
            lambda command, timeout: subprocess.CompletedProcess(
                command, 0, "malformed", ""
            ),
            "nvidia_smi_malformed",
        ),
        (
            lambda command, timeout: (_ for _ in ()).throw(
                subprocess.TimeoutExpired(command, timeout)
            ),
            "nvidia_smi_timeout",
        ),
    ],
)
def test_hardware_probe_returns_typed_partial_state(runner, error_code) -> None:
    snapshot = collect_hardware_snapshot(
        torch_module=_UnavailableTorch,
        command_runner=runner,
    )
    assert snapshot.gpu_devices == ()
    assert snapshot.partial is True
    assert error_code in snapshot.errors


def test_no_nvidia_gpu_is_unknown_not_fabricated_zero() -> None:
    snapshot = collect_hardware_snapshot(
        torch_module=_UnavailableTorch, nvidia_smi_available=False
    )
    assert snapshot.gpu_devices == ()
    assert snapshot.total_vram_bytes is None
    assert snapshot.free_vram_bytes is None
    report = snapshot_to_hardware_report(snapshot)
    assert report.gpu.vram_total_mb is None
    assert report.gpu.vram_free_mb is None


def test_hardware_snapshot_cache_expires_deterministically() -> None:
    clock = [100.0]
    calls = 0

    def probe():
        nonlocal calls
        calls += 1
        return _snapshot(cpu_name=f"CPU {calls}")

    registry = HardwareCapabilityRegistry(
        probe, ttl_seconds=5, monotonic=lambda: clock[0]
    )
    assert registry.snapshot().cpu_name == "CPU 1"
    clock[0] = 104.9
    assert registry.snapshot().cpu_name == "CPU 1"
    clock[0] = 105.0
    assert registry.snapshot().cpu_name == "CPU 2"
    assert calls == 2


def test_capability_probe_cannot_create_scheduler_work(tmp_path: Path) -> None:
    service = _service(
        tmp_path,
        ollama_probe=lambda _configuration: (False, (), (), "provider_unreachable"),
    )
    service.capability_report(refresh=True)
    assert service.scheduler_jobs() == ()


def test_durable_invocation_records_exact_endpoint_and_model(tmp_path: Path) -> None:
    database = tmp_path / "invocation.db"
    control = ProjectControlPlane(database)
    control.initialize()
    control.execute(
        ProjectCommand(
            command_type=ProjectCommandType.INITIALIZE_PROJECT,
            project_run_id="project-1",
            conversation_id="conversation-1",
            workspace_id="workspace-1",
            repository_root="canonical-root",
            repository_root_fingerprint="root-fingerprint",
            actor_id="local-user",
            expected_state_version=0,
            idempotency_key="initialize-phase4b-invocation",
        )
    )
    store = ProjectModelInvocationStore(database)
    store.initialize()
    invocation = build_project_model_invocation(
        project_run_id="project-1",
        coordinator_intent_id="intent-1",
        purpose="prepare_patch",
        evidence_hash="a" * 64,
        provider="ollama",
        model_profile="configured-local-model",
        provider_endpoint_identity="http://127.0.0.1:11434",
        provider_model_id="qwen2.5-coder:1.5b",
        request_payload={"bounded": True},
    )
    stored = store.create(invocation)
    replayed = store.get(stored.invocation_id)
    assert replayed is not None
    assert replayed.provider_endpoint_identity == "http://127.0.0.1:11434"
    assert replayed.provider_model_id == "qwen2.5-coder:1.5b"
