from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.database.migrations import apply_schema_migrations
from backend.app.local_ai.config import load_local_ai_configuration
from backend.app.local_ai.contracts import (
    CapabilityStatus,
    MemoryCapability,
    ModelConfigurationResult,
    ModelProfile,
    OllamaCapability,
    VRAMCapability,
)
from backend.app.local_ai.service import LocalAIService
from backend.app.main import create_app


GIB = 1024**3
MODEL = "qwen2.5-coder:1.5b"


def _runtime_capabilities(*, reachable: bool = True, installed: tuple[str, ...] = (MODEL,)):
    now = datetime.now(timezone.utc)
    return (
        MemoryCapability(
            capability_id="memory", status=CapabilityStatus.AVAILABLE,
            total_bytes=16 * GIB, available_bytes=8 * GIB, probed_at=now,
        ),
        VRAMCapability(
            capability_id="vram", status=CapabilityStatus.AVAILABLE,
            total_bytes=4 * GIB, free_bytes=3 * GIB, probed_at=now,
        ),
        OllamaCapability(
            capability_id="ollama",
            status=(
                CapabilityStatus.AVAILABLE if reachable and MODEL in installed
                else CapabilityStatus.UNAVAILABLE
            ),
            endpoint="http://127.0.0.1:11434", configured_models=(MODEL,),
            installed_models=installed, loaded_models=(),
            provider_reachable=reachable,
            configured_model_missing=reachable and MODEL not in installed,
            probed_at=now,
        ),
    )


def _service(tmp_path: Path, probe) -> LocalAIService:
    database = tmp_path / "config-version.db"
    apply_schema_migrations(database)
    configuration = load_local_ai_configuration({
        "ASTRA_LOCAL_AI_GENERATION_ENABLED": "true",
        "ASTRA_LOCAL_AI_MODEL": MODEL,
    })
    service = LocalAIService(database, probe=probe, configuration=configuration)
    service.initialize()
    return service


def _row(database: Path, model_profile_id: str) -> sqlite3.Row:
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        return connection.execute(
            "SELECT config_version, updated_at FROM local_ai_models WHERE model_profile_id = ?",
            (model_profile_id,),
        ).fetchone()
    finally:
        connection.close()


def test_models_includes_configuration_version_matching_the_database_row(tmp_path: Path) -> None:
    service = _service(tmp_path, lambda: _runtime_capabilities())
    models = {item.model_profile_id: item for item in service.models()}
    configured = models["configured-local-model"]
    assert isinstance(configured, ModelConfigurationResult)

    row = _row(service.database_path, "configured-local-model")
    assert configured.configuration_version == int(row["config_version"])


def test_models_includes_updated_at(tmp_path: Path) -> None:
    service = _service(tmp_path, lambda: _runtime_capabilities())
    configured = next(item for item in service.models() if item.model_profile_id == "configured-local-model")
    row = _row(service.database_path, "configured-local-model")
    assert configured.updated_at is not None
    assert str(configured.updated_at) == str(row["updated_at"]) or configured.updated_at.isoformat().startswith(
        str(row["updated_at"])[:19]
    )


def test_multiple_model_profiles_retain_independent_versions(tmp_path: Path) -> None:
    service = _service(tmp_path, lambda: _runtime_capabilities())
    service.capability_report(refresh=True)
    models = {item.model_profile_id: item for item in service.models()}
    before_versions = {key: item.configuration_version for key, item in models.items()}
    assert len(set(before_versions.values())) >= 1  # sanity: all seeded at version 1 initially

    service.set_model_enabled(
        "configured-local-model", enabled=True, actor_id="local-user",
        expected_version=before_versions["configured-local-model"], idempotency_key="enable-configured",
    )
    after = {item.model_profile_id: item.configuration_version for item in service.models()}
    assert after["configured-local-model"] == before_versions["configured-local-model"] + 1
    # Every other model profile's version is untouched by this specific mutation.
    for profile_id, version in before_versions.items():
        if profile_id == "configured-local-model":
            continue
        assert after[profile_id] == version


def test_client_can_list_then_enable_using_the_returned_version(tmp_path: Path) -> None:
    service = _service(tmp_path, lambda: _runtime_capabilities())
    service.capability_report(refresh=True)
    configured = next(item for item in service.models() if item.model_profile_id == "configured-local-model")

    result = service.set_model_enabled(
        configured.model_profile_id, enabled=True, actor_id="local-user",
        expected_version=configured.configuration_version, idempotency_key="round-trip-enable",
    )
    assert result.enabled is True
    assert result.configuration_version == configured.configuration_version + 1


def test_stale_version_from_a_prior_list_still_returns_the_existing_conflict(tmp_path: Path) -> None:
    service = _service(tmp_path, lambda: _runtime_capabilities())
    service.capability_report(refresh=True)
    configured = next(item for item in service.models() if item.model_profile_id == "configured-local-model")

    service.set_model_enabled(
        configured.model_profile_id, enabled=True, actor_id="local-user",
        expected_version=configured.configuration_version, idempotency_key="first-enable",
    )
    try:
        service.set_model_enabled(
            configured.model_profile_id, enabled=False, actor_id="local-user",
            expected_version=configured.configuration_version,  # now stale
            idempotency_key="second-disable-with-stale-version",
        )
        raise AssertionError("expected stale_configuration_version to be raised")
    except ValueError as exc:
        assert str(exc) == "stale_configuration_version"


def test_listing_models_does_not_alter_config_version(tmp_path: Path) -> None:
    service = _service(tmp_path, lambda: _runtime_capabilities())
    before = _row(service.database_path, "configured-local-model")
    service.models()
    service.models()
    service.models()
    after = _row(service.database_path, "configured-local-model")
    assert int(after["config_version"]) == int(before["config_version"])
    assert str(after["updated_at"]) == str(before["updated_at"])


def test_existing_model_profile_field_access_remains_unaffected(tmp_path: Path) -> None:
    service = _service(tmp_path, lambda: _runtime_capabilities())
    configured = next(item for item in service.models() if item.model_profile_id == "configured-local-model")
    # Every ModelProfile field a pre-existing consumer relied on must still resolve.
    assert isinstance(configured, ModelProfile)
    assert configured.provider_id == "ollama-local"
    assert configured.provider_model_id == MODEL
    assert configured.display_name
    assert configured.enabled is False


def test_http_endpoint_includes_configuration_version_and_updated_at(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path / "config-version-api.db", tmp_path)) as client:
        response = client.get("/runtime/local-ai/models")
        assert response.status_code == 200
        body = response.json()
        assert body["schema_version"] == "astra.local-ai.models.v1"
        configured = next(
            item for item in body["items"] if item["model_profile_id"] == "configured-local-model"
        )
        assert isinstance(configured["configuration_version"], int)
        assert configured["configuration_version"] >= 1
        assert configured["updated_at"] is not None


def test_http_endpoint_returned_version_enables_the_model_end_to_end(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(LocalAIService, "_safe_probe", lambda _self: _runtime_capabilities())
    with TestClient(create_app(tmp_path / "config-version-api2.db", tmp_path)) as client:
        assert client.get("/runtime/local-ai/capabilities").status_code == 200
        listed = next(
            item for item in client.get("/runtime/local-ai/models").json()["items"]
            if item["model_profile_id"] == "configured-local-model"
        )
        response = client.post(
            "/runtime/local-ai/models/configured-local-model/enabled",
            json={
                "actor_id": "local-user",
                "enabled": True,
                "expected_configuration_version": listed["configuration_version"],
                "idempotency_key": "http-round-trip-enable",
            },
        )
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["enabled"] is True
        assert result["configuration_version"] == listed["configuration_version"] + 1


def test_http_endpoint_stale_version_still_returns_409(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(LocalAIService, "_safe_probe", lambda _self: _runtime_capabilities())
    with TestClient(create_app(tmp_path / "config-version-api3.db", tmp_path)) as client:
        client.get("/runtime/local-ai/capabilities")
        listed = next(
            item for item in client.get("/runtime/local-ai/models").json()["items"]
            if item["model_profile_id"] == "configured-local-model"
        )
        stale_version = listed["configuration_version"]
        first = client.post(
            "/runtime/local-ai/models/configured-local-model/enabled",
            json={
                "actor_id": "local-user", "enabled": True,
                "expected_configuration_version": stale_version,
                "idempotency_key": "first-http-enable",
            },
        )
        assert first.status_code == 200

        second = client.post(
            "/runtime/local-ai/models/configured-local-model/enabled",
            json={
                "actor_id": "local-user", "enabled": False,
                "expected_configuration_version": stale_version,
                "idempotency_key": "second-http-enable-stale",
            },
        )
        assert second.status_code == 409
        assert second.json()["detail"]["code"] == "stale_configuration_version"
