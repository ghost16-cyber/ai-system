from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
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
MODEL_TAG = "qwen2.5-coder:1.5b"
MODEL_PROFILE_ID = "configured-local-model"


def _runtime_capabilities():
    now = datetime.now(timezone.utc)
    return (
        MemoryCapability(
            capability_id="memory",
            status=CapabilityStatus.AVAILABLE,
            total_bytes=16 * GIB,
            available_bytes=8 * GIB,
            probed_at=now,
        ),
        VRAMCapability(
            capability_id="vram",
            status=CapabilityStatus.AVAILABLE,
            total_bytes=4 * GIB,
            free_bytes=3 * GIB,
            probed_at=now,
        ),
        OllamaCapability(
            capability_id="ollama",
            status=CapabilityStatus.AVAILABLE,
            endpoint="http://127.0.0.1:11434",
            configured_models=(MODEL_TAG,),
            installed_models=(MODEL_TAG,),
            provider_reachable=True,
            probed_at=now,
        ),
    )


def _service(tmp_path: Path) -> LocalAIService:
    database = tmp_path / "stage7c.db"
    apply_schema_migrations(database)
    service = LocalAIService(
        database,
        probe=_runtime_capabilities,
        configuration=load_local_ai_configuration(
            {
                "ASTRA_LOCAL_AI_GENERATION_ENABLED": "true",
                "ASTRA_LOCAL_AI_MODEL": MODEL_TAG,
            }
        ),
    )
    service.initialize()
    return service


def test_configuration_read_exposes_exact_durable_resource_versions(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    state = service.configuration_state()

    assert state.configuration_version.model_profiles[MODEL_PROFILE_ID] == 1
    assert state.configuration_version.role_mappings == {}
    assert state.configuration_version.unmapped_role_configuration_version == 0
    assert state.generation_enabled is True
    assert "fake-deterministic" in state.enabled_model_profile_ids
    assert state.role_mappings == {}


def test_returned_model_version_drives_mutation_and_replay_is_stable(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.capability_report(refresh=True)
    version = service.configuration_state().configuration_version.model_profiles[
        MODEL_PROFILE_ID
    ]

    first = service.set_model_enabled(
        MODEL_PROFILE_ID,
        enabled=True,
        actor_id="local-user",
        expected_version=version,
        idempotency_key="enable-stage7c",
    )
    replay = service.set_model_enabled(
        MODEL_PROFILE_ID,
        enabled=True,
        actor_id="local-user",
        expected_version=version,
        idempotency_key="enable-stage7c",
    )

    assert first.configuration_version == version + 1
    assert replay == first
    assert (
        service.configuration_state().configuration_version.model_profiles[
            MODEL_PROFILE_ID
        ]
        == version + 1
    )
    with pytest.raises(ValueError, match="stale_configuration_version"):
        service.set_model_enabled(
            MODEL_PROFILE_ID,
            enabled=False,
            actor_id="local-user",
            expected_version=version,
            idempotency_key="stale-stage7c",
        )


def test_returned_role_version_drives_role_mapping_and_replay(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    initial = service.configuration_state()

    expected = initial.configuration_version.unmapped_role_configuration_version
    first = service.set_role_mapping(
        "coding",
        "fake-deterministic",
        actor_id="local-user",
        expected_version=expected,
        idempotency_key="map-coding",
    )
    replay = service.set_role_mapping(
        "coding",
        "fake-deterministic",
        actor_id="local-user",
        expected_version=expected,
        idempotency_key="map-coding",
    )
    state = service.configuration_state()

    assert first.configuration_version == 1
    assert replay == first
    assert state.configuration_version.role_mappings["coding"] == 1
    assert state.role_mappings["coding"] == "fake-deterministic"


def test_provider_enablement_is_not_an_execution_gate(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.capability_report(refresh=True)
    before = service.configuration_state()
    provider_before = next(
        item for item in service.providers() if item.provider_id == "ollama-local"
    )

    result = service.set_model_enabled(
        MODEL_PROFILE_ID,
        enabled=True,
        actor_id="local-user",
        expected_version=before.configuration_version.model_profiles[
            MODEL_PROFILE_ID
        ],
        idempotency_key="provider-independent-model-enable",
    )
    provider_after = next(
        item for item in service.providers() if item.provider_id == "ollama-local"
    )

    assert before.provider_enablement_mode == "not_an_execution_gate"
    assert provider_before.enabled is False
    assert provider_after.enabled is False
    assert result.enabled is True
    assert result.policy_status == CapabilityStatus.READY


def test_capability_refresh_and_registry_reads_do_not_change_versions(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    before = service.configuration_state()
    first_report = service.capability_report(refresh=True)

    service.refresh_capabilities(
        actor_id="local-user",
        expected_snapshot_id=first_report.report_id,
        idempotency_key="refresh-without-config-write",
    )
    service.providers()
    service.models()
    service.providers()
    service.models()
    after = service.configuration_state()

    assert after.configuration_version == before.configuration_version
    assert after.updated_at == before.updated_at


def test_configuration_api_returns_versions_accepted_by_mutations(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ASTRA_LOCAL_AI_GENERATION_ENABLED", "true")
    monkeypatch.setenv("ASTRA_LOCAL_AI_MODEL", MODEL_TAG)
    monkeypatch.setattr(LocalAIService, "_safe_probe", lambda _self: _runtime_capabilities())

    with TestClient(create_app(tmp_path / "stage7c-api.db", tmp_path)) as client:
        assert client.get("/runtime/local-ai/capabilities").status_code == 200
        configuration = client.get("/runtime/local-ai/configuration")
        assert configuration.status_code == 200
        body = configuration.json()
        version = body["configuration_version"]["model_profiles"][MODEL_PROFILE_ID]

        enabled = client.post(
            f"/runtime/local-ai/models/{MODEL_PROFILE_ID}/enabled",
            json={
                "actor_id": "api-user",
                "enabled": True,
                "expected_configuration_version": version,
                "idempotency_key": "api-enable-stage7c",
            },
        )
        assert enabled.status_code == 200
        assert enabled.json()["configuration_version"] == version + 1

        mapped = client.post(
            "/runtime/local-ai/roles/coding",
            json={
                "actor_id": "api-user",
                "model_profile_id": MODEL_PROFILE_ID,
                "expected_configuration_version": body["configuration_version"][
                    "unmapped_role_configuration_version"
                ],
                "idempotency_key": "api-role-stage7c",
            },
        )
        assert mapped.status_code == 200
        assert mapped.json()["configuration_version"] == 1
