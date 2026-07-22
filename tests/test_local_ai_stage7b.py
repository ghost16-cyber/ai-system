from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.database.migrations import apply_schema_migrations
from backend.app.local_ai.config import load_local_ai_configuration
from backend.app.local_ai.contracts import (
    CapabilityStatus,
    MemoryCapability,
    OllamaCapability,
    VRAMCapability,
)
from backend.app.local_ai.service import LocalAIService
from backend.app.main import create_app


GIB = 1024**3
MODEL = "qwen2.5-coder:1.5b"


def _runtime_capabilities(
    *,
    reachable: bool = True,
    installed: tuple[str, ...] = (MODEL,),
    available_ram: int = 8 * GIB,
    available_vram: int = 3 * GIB,
):
    now = datetime.now(timezone.utc)
    return (
        MemoryCapability(
            capability_id="memory",
            status=CapabilityStatus.AVAILABLE,
            total_bytes=16 * GIB,
            available_bytes=available_ram,
            probed_at=now,
        ),
        VRAMCapability(
            capability_id="vram",
            status=CapabilityStatus.AVAILABLE,
            total_bytes=4 * GIB,
            free_bytes=available_vram,
            probed_at=now,
        ),
        OllamaCapability(
            capability_id="ollama",
            status=(
                CapabilityStatus.AVAILABLE
                if reachable and MODEL in installed
                else CapabilityStatus.UNAVAILABLE
            ),
            endpoint="http://127.0.0.1:11434",
            configured_models=(MODEL,),
            installed_models=installed,
            loaded_models=(),
            provider_reachable=reachable,
            configured_model_missing=reachable and MODEL not in installed,
            probed_at=now,
        ),
    )


def _service(tmp_path: Path, probe, *, generation_enabled: bool = True):
    database = tmp_path / "stage7b.db"
    apply_schema_migrations(database)
    configuration = load_local_ai_configuration(
        {
            "ASTRA_LOCAL_AI_GENERATION_ENABLED": (
                "true" if generation_enabled else "false"
            ),
            "ASTRA_LOCAL_AI_MODEL": MODEL,
        }
    )
    service = LocalAIService(
        database,
        probe=probe,
        configuration=configuration,
    )
    service.initialize()
    return service


def _configured_model(service: LocalAIService):
    return next(
        item
        for item in service.models()
        if item.model_profile_id == "configured-local-model"
    )


def test_reachable_installed_model_is_available_but_not_auto_enabled(tmp_path: Path) -> None:
    service = _service(tmp_path, lambda: _runtime_capabilities())
    service.capability_report(refresh=True)
    model = _configured_model(service)
    provider = next(item for item in service.providers() if item.provider_id == "ollama-local")
    assert model.local_available is True
    assert model.policy_status == CapabilityStatus.INSTALLED_NOT_ENABLED
    assert model.enabled is False
    assert provider.health_status == CapabilityStatus.AVAILABLE
    assert provider.enabled is False

    enabled = service.set_model_enabled(
        model.model_profile_id,
        enabled=True,
        actor_id="local-user",
        expected_version=1,
        idempotency_key="explicit-enable",
    )
    assert enabled.enabled is True
    assert enabled.policy_status == CapabilityStatus.READY


def test_reachable_provider_with_missing_model_is_not_installed(tmp_path: Path) -> None:
    service = _service(
        tmp_path,
        lambda: _runtime_capabilities(installed=("another:1b",)),
    )
    service.capability_report(refresh=True)
    model = _configured_model(service)
    assert model.local_available is False
    assert model.policy_status == CapabilityStatus.MODEL_NOT_INSTALLED
    provider = next(item for item in service.providers() if item.provider_id == "ollama-local")
    assert provider.health_status == CapabilityStatus.AVAILABLE


def test_unreachable_provider_is_effective_runtime_state(tmp_path: Path) -> None:
    service = _service(
        tmp_path,
        lambda: _runtime_capabilities(reachable=False, installed=()),
    )
    service.capability_report(refresh=True)
    model = _configured_model(service)
    assert model.local_available is False
    assert model.policy_status == CapabilityStatus.PROVIDER_UNREACHABLE
    provider = next(item for item in service.providers() if item.provider_id == "ollama-local")
    assert provider.health_status == CapabilityStatus.UNAVAILABLE


def test_insufficient_ram_is_policy_state_without_hiding_installation(tmp_path: Path) -> None:
    service = _service(
        tmp_path,
        lambda: _runtime_capabilities(available_ram=3 * GIB),
    )
    service.capability_report(refresh=True)
    model = _configured_model(service)
    assert model.local_available is True
    assert model.policy_status == CapabilityStatus.INSUFFICIENT_MEMORY
    assert model.enabled is False


def test_insufficient_vram_is_policy_state_without_hiding_installation(tmp_path: Path) -> None:
    service = _service(
        tmp_path,
        lambda: _runtime_capabilities(available_vram=1 * GIB),
    )
    service.capability_report(refresh=True)
    model = _configured_model(service)
    assert model.local_available is True
    assert model.policy_status == CapabilityStatus.INSUFFICIENT_VRAM
    assert model.enabled is False


def test_global_generation_policy_can_disable_installed_model(tmp_path: Path) -> None:
    service = _service(
        tmp_path,
        lambda: _runtime_capabilities(),
        generation_enabled=False,
    )
    service.capability_report(refresh=True)
    model = _configured_model(service)
    assert model.local_available is True
    assert model.policy_status == CapabilityStatus.DISABLED_BY_POLICY
    assert model.enabled is False


def test_runtime_refresh_updates_model_availability(tmp_path: Path) -> None:
    runtime = {"installed": ()}

    def probe():
        return _runtime_capabilities(installed=runtime["installed"])

    service = _service(tmp_path, probe)
    service.capability_report(refresh=True)
    assert _configured_model(service).policy_status == CapabilityStatus.MODEL_NOT_INSTALLED
    runtime["installed"] = (MODEL,)
    service.capability_report(refresh=True)
    refreshed = _configured_model(service)
    assert refreshed.local_available is True
    assert refreshed.policy_status == CapabilityStatus.INSTALLED_NOT_ENABLED


def test_explicit_capability_refresh_propagates_into_registry(tmp_path: Path) -> None:
    runtime = {"reachable": False, "installed": ()}

    def probe():
        return _runtime_capabilities(
            reachable=runtime["reachable"],
            installed=runtime["installed"],
        )

    service = _service(tmp_path, probe)
    first = service.capability_report(refresh=True)
    assert _configured_model(service).policy_status == CapabilityStatus.PROVIDER_UNREACHABLE
    runtime.update({"reachable": True, "installed": (MODEL,)})
    service.refresh_capabilities(
        actor_id="local-user",
        expected_snapshot_id=first.report_id,
        idempotency_key="refresh-stage7b",
    )
    assert _configured_model(service).local_available is True
    assert next(
        item for item in service.providers() if item.provider_id == "ollama-local"
    ).health_status == CapabilityStatus.AVAILABLE


def test_fake_provider_and_model_are_unaffected(tmp_path: Path) -> None:
    service = _service(
        tmp_path,
        lambda: _runtime_capabilities(reachable=False, installed=()),
    )
    service.capability_report(refresh=True)
    fake_model = next(
        item for item in service.models() if item.model_profile_id == "fake-deterministic"
    )
    fake_provider = next(
        item for item in service.providers() if item.provider_id == "fake-deterministic"
    )
    assert fake_model.local_available is True
    assert fake_model.policy_status == CapabilityStatus.AVAILABLE
    assert fake_model.enabled is True
    assert fake_provider.health_status == CapabilityStatus.AVAILABLE
    assert fake_provider.enabled is True


def test_model_and_provider_endpoints_consume_latest_capability_snapshot(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        LocalAIService,
        "_safe_probe",
        lambda _self: _runtime_capabilities(),
    )
    with TestClient(create_app(tmp_path / "stage7b-api.db", tmp_path)) as client:
        before = client.get("/runtime/local-ai/models").json()["items"]
        configured_before = next(
            item for item in before if item["model_profile_id"] == "configured-local-model"
        )
        assert configured_before["policy_status"] == "not_configured"
        assert client.get("/runtime/local-ai/capabilities").status_code == 200
        after = client.get("/runtime/local-ai/models").json()["items"]
        configured_after = next(
            item for item in after if item["model_profile_id"] == "configured-local-model"
        )
        assert configured_after["local_available"] is True
        assert configured_after["policy_status"] == "installed_not_enabled"
        provider = next(
            item
            for item in client.get("/runtime/local-ai/providers").json()["items"]
            if item["provider_id"] == "ollama-local"
        )
        assert provider["health_status"] == "available"
        assert provider["enabled"] is False
