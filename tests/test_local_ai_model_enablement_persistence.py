from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.app.database.migrations import apply_schema_migrations
from backend.app.local_ai.config import LocalAIConfiguration
from backend.app.local_ai.contracts import (
    CapabilityStatus,
    MemoryCapability,
    OllamaCapability,
    VRAMCapability,
)
from backend.app.local_ai.service import LocalAIService


MODEL_PROFILE_ID = "configured-local-model"
MODEL_TAG = "qwen2.5-coder:1.5b"


def _configuration(**overrides) -> LocalAIConfiguration:
    values = dict(
        generation_enabled=True, project_synthesis_enabled=True,
        provider_type="ollama", endpoint_identity="http://127.0.0.1:11434",
        synthesis_model=MODEL_TAG, coder_model=MODEL_TAG,
        planner_model=MODEL_TAG, reviewer_model=MODEL_TAG,
        connection_timeout_seconds=2, generation_timeout_seconds=10,
        maximum_context_tokens=4096, maximum_output_tokens=512,
        allow_cpu_fallback=False, gpu_exclusive_concurrency=True,
    )
    values.update(overrides)
    return LocalAIConfiguration(**values)


def _capabilities(model_tag: str = MODEL_TAG):
    now = datetime.now(timezone.utc)
    return (
        MemoryCapability(
            capability_id="memory", status=CapabilityStatus.AVAILABLE,
            total_bytes=16 * 1024**3, available_bytes=12 * 1024**3, probed_at=now,
        ),
        VRAMCapability(
            capability_id="vram", status=CapabilityStatus.AVAILABLE,
            total_bytes=4 * 1024**3, free_bytes=4 * 1024**3, probed_at=now,
        ),
        OllamaCapability(
            capability_id="ollama", status=CapabilityStatus.AVAILABLE,
            endpoint="http://127.0.0.1:11434", configured_models=(model_tag,),
            installed_models=(model_tag,), provider_reachable=True, probed_at=now,
        ),
    )


def _service(database: Path, configuration: LocalAIConfiguration | None = None) -> LocalAIService:
    configuration = configuration or _configuration()
    service = LocalAIService(
        database, configuration=configuration,
        probe=lambda: _capabilities(configuration.synthesis_model),
    )
    service.initialize()
    return service


def _model_row(database: Path) -> dict:
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    row = connection.execute(
        "SELECT * FROM local_ai_models WHERE model_profile_id = ?", (MODEL_PROFILE_ID,)
    ).fetchone()
    connection.close()
    return dict(row)


def _current_version(service: LocalAIService) -> int:
    return service.configuration_state().configuration_version.model_profiles[MODEL_PROFILE_ID]


def test_missing_profile_is_seeded_disabled_by_default(tmp_path: Path) -> None:
    database = tmp_path / "seed.db"
    apply_schema_migrations(database)
    _service(database)

    row = _model_row(database)
    assert row["enabled"] == 0
    assert row["config_version"] == 1
    assert json.loads(row["profile_json"])["enabled"] is False


def test_explicitly_enabled_profile_remains_enabled_after_restart(tmp_path: Path) -> None:
    database = tmp_path / "restart-enabled.db"
    apply_schema_migrations(database)
    service = _service(database)
    service.capability_report(refresh=True)
    version = _current_version(service)
    service.set_model_enabled(
        MODEL_PROFILE_ID, enabled=True, actor_id="test-user",
        expected_version=version, idempotency_key="enable-restart-1",
    )

    restarted = LocalAIService(database, configuration=_configuration(), probe=lambda: _capabilities())
    restarted.initialize()

    row = _model_row(database)
    assert row["enabled"] == 1
    assert json.loads(row["profile_json"])["enabled"] is True
    models = {model.model_profile_id: model for model in restarted.models()}
    assert models[MODEL_PROFILE_ID].enabled is True


def test_explicitly_disabled_profile_remains_disabled_after_restart(tmp_path: Path) -> None:
    database = tmp_path / "restart-disabled.db"
    apply_schema_migrations(database)
    service = _service(database)
    service.capability_report(refresh=True)
    version = _current_version(service)
    enabled_result = service.set_model_enabled(
        MODEL_PROFILE_ID, enabled=True, actor_id="test-user",
        expected_version=version, idempotency_key="enable-restart-2",
    )
    disabled_result = service.set_model_enabled(
        MODEL_PROFILE_ID, enabled=False, actor_id="test-user",
        expected_version=enabled_result.configuration_version,
        idempotency_key="disable-restart-2",
    )

    restarted = LocalAIService(database, configuration=_configuration(), probe=lambda: _capabilities())
    restarted.initialize()

    row = _model_row(database)
    assert row["enabled"] == 0
    assert row["config_version"] == disabled_result.configuration_version


def test_noop_restart_does_not_increment_config_version(tmp_path: Path) -> None:
    database = tmp_path / "noop-restart.db"
    apply_schema_migrations(database)
    service = _service(database)
    service.capability_report(refresh=True)
    version = _current_version(service)
    enabled_result = service.set_model_enabled(
        MODEL_PROFILE_ID, enabled=True, actor_id="test-user",
        expected_version=version, idempotency_key="enable-noop",
    )

    for _ in range(3):
        restarted = LocalAIService(database, configuration=_configuration(), probe=lambda: _capabilities())
        restarted.initialize()

    row = _model_row(database)
    assert row["config_version"] == enabled_result.configuration_version
    assert row["enabled"] == 1


def test_configuration_derived_change_is_reconciled_preserving_enabled_true(tmp_path: Path) -> None:
    database = tmp_path / "reconcile-true.db"
    apply_schema_migrations(database)
    service = _service(database)
    service.capability_report(refresh=True)
    version = _current_version(service)
    enabled_result = service.set_model_enabled(
        MODEL_PROFILE_ID, enabled=True, actor_id="test-user",
        expected_version=version, idempotency_key="enable-reconcile-true",
    )

    changed_configuration = _configuration(maximum_output_tokens=1024)
    restarted = LocalAIService(database, configuration=changed_configuration, probe=lambda: _capabilities())
    restarted.initialize()

    row = _model_row(database)
    profile = json.loads(row["profile_json"])
    assert profile["maximum_output_tokens"] == 1024
    assert profile["enabled"] is True
    assert row["enabled"] == 1
    assert row["config_version"] == enabled_result.configuration_version + 1


def test_configuration_derived_change_is_reconciled_preserving_enabled_false(tmp_path: Path) -> None:
    database = tmp_path / "reconcile-false.db"
    apply_schema_migrations(database)
    service = _service(database)
    version_before = _current_version(service)

    changed_configuration = _configuration(maximum_output_tokens=1024)
    restarted = LocalAIService(database, configuration=changed_configuration, probe=lambda: _capabilities())
    restarted.initialize()

    row = _model_row(database)
    profile = json.loads(row["profile_json"])
    assert profile["maximum_output_tokens"] == 1024
    assert profile["enabled"] is False
    assert row["enabled"] == 0
    assert row["config_version"] == version_before + 1


def test_stale_expected_version_still_rejected_after_restart(tmp_path: Path) -> None:
    database = tmp_path / "stale-after-restart.db"
    apply_schema_migrations(database)
    service = _service(database)
    service.capability_report(refresh=True)
    pre_enable_version = _current_version(service)
    service.set_model_enabled(
        MODEL_PROFILE_ID, enabled=True, actor_id="test-user",
        expected_version=pre_enable_version, idempotency_key="enable-stale-check",
    )

    restarted = LocalAIService(database, configuration=_configuration(), probe=lambda: _capabilities())
    restarted.initialize()

    with pytest.raises(ValueError, match="stale_configuration_version"):
        restarted.set_model_enabled(
            MODEL_PROFILE_ID, enabled=False, actor_id="test-user",
            expected_version=pre_enable_version, idempotency_key="stale-attempt-after-restart",
        )


def test_enable_disable_audit_events_are_correct_and_restart_adds_none(tmp_path: Path) -> None:
    database = tmp_path / "audit.db"
    apply_schema_migrations(database)
    service = _service(database)
    service.capability_report(refresh=True)
    version = _current_version(service)
    enabled_result = service.set_model_enabled(
        MODEL_PROFILE_ID, enabled=True, actor_id="user-a",
        expected_version=version, idempotency_key="enable-audit",
    )
    service.set_model_enabled(
        MODEL_PROFILE_ID, enabled=False, actor_id="user-b",
        expected_version=enabled_result.configuration_version, idempotency_key="disable-audit",
    )

    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        "SELECT event_type, event_json FROM local_ai_audit_events "
        "WHERE aggregate_id = ? ORDER BY created_at",
        (MODEL_PROFILE_ID,),
    ).fetchall()
    connection.close()
    events = [(row["event_type"], json.loads(row["event_json"])) for row in rows]
    assert events == [
        ("model_configuration_changed", {"enabled": True, "actor_id": "user-a"}),
        ("model_configuration_changed", {"enabled": False, "actor_id": "user-b"}),
    ]

    for _ in range(2):
        restarted = LocalAIService(database, configuration=_configuration(), probe=lambda: _capabilities())
        restarted.initialize()

    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    after = connection.execute(
        "SELECT COUNT(*) AS count FROM local_ai_audit_events WHERE aggregate_id = ?",
        (MODEL_PROFILE_ID,),
    ).fetchone()
    connection.close()
    assert after["count"] == 2


def test_fake_deterministic_profile_is_unaffected_by_restart_reconciliation(tmp_path: Path) -> None:
    database = tmp_path / "fake-deterministic.db"
    apply_schema_migrations(database)
    _service(database)

    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    before = dict(connection.execute(
        "SELECT * FROM local_ai_models WHERE model_profile_id = 'fake-deterministic'"
    ).fetchone())
    connection.close()
    assert before["enabled"] == 1
    assert before["config_version"] == 1

    for _ in range(3):
        restarted = LocalAIService(database, configuration=_configuration(), probe=lambda: _capabilities())
        restarted.initialize()

    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    after = dict(connection.execute(
        "SELECT * FROM local_ai_models WHERE model_profile_id = 'fake-deterministic'"
    ).fetchone())
    connection.close()
    assert after == before
