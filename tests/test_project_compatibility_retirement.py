from __future__ import annotations

import pytest

from backend.app.project_artifacts import ProjectArtifactStore
from backend.app.project_control import ProjectControlError, ProjectControlErrorCode, ProjectControlPlane
from backend.app.project_control.adapters import ProjectDeliveryControlAdapter
from backend.app.project_workers.__main__ import build_runtime


def test_historical_delivery_is_read_only_without_get_side_import(tmp_path) -> None:
    database = tmp_path / "astra.db"
    artifacts = ProjectArtifactStore(database)
    control = ProjectControlPlane(database, artifact_store=artifacts)
    control.initialize()
    artifacts.initialize()
    adapter = ProjectDeliveryControlAdapter(control, artifacts)
    historical = {"delivery_job_id": "historical-1", "conversation_id": "conversation-1"}
    decorated = adapter.decorate(historical, tmp_path)
    assert decorated["record_generation"] == "legacy"
    assert decorated["historical_read_only"] is True
    assert control.list_projects_for_conversation("conversation-1") == []
    with pytest.raises(ProjectControlError) as caught:
        adapter.apply_transition(decorated, decorated, tmp_path, "cancellation")
    assert caught.value.code == ProjectControlErrorCode.HISTORICAL_RECORD_READ_ONLY


def test_retired_host_backend_cannot_be_reenabled_by_environment(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ASTRA_ALLOW_LEGACY_PROJECT_EXECUTION", "1")
    with pytest.raises(RuntimeError, match="has been retired"):
        build_runtime(
            database_path=tmp_path / "astra.db", workspace_root=tmp_path,
            execution_backend="legacy",
        )
