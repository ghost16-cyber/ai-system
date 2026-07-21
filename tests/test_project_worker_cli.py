from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.app.project_workers.__main__ import (
    build_runtime,
    clamp_idle_seconds,
    main,
    report_has_activity,
    worker_cycle,
)


class FakeService:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def recover_expired_leases(self):
        self.calls.append("recover")
        return SimpleNamespace(
            recovered_request_ids=("request-recovered",),
            canonical_recovery_ids=("request-reconciled",),
        )

    def dispatch_pending(self):
        self.calls.append("dispatch")
        return SimpleNamespace(
            dispatched_request_ids=("request-dispatched",),
            recovered_dispatch_ids=("dispatch-recovered",),
            deferred_dispatch_ids=("dispatch-deferred",),
        )


class FakeExecutor:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    def run_once(self, worker_id: str) -> bool:
        self.calls.append(f"execute:{worker_id}")
        return True


def test_worker_cycle_recovers_outbox_then_terminal_results_before_execution() -> None:
    service = FakeService()
    report = worker_cycle(
        service,
        worker_id="worker-one",
        executor=FakeExecutor(service.calls),
    )

    assert service.calls == ["dispatch", "recover", "execute:worker-one"]
    assert report["dispatched_request_ids"] == ["request-dispatched"]
    assert report["recovered_dispatch_ids"] == ["dispatch-recovered"]
    assert report["executed"] is True


def test_docker_selection_fails_closed_without_host_fallback(tmp_path: Path, monkeypatch) -> None:
    class UnavailableDocker:
        def __init__(self, _profile) -> None:
            pass

        def probe(self):
            return SimpleNamespace(
                available=False,
                failure_code="image_digest_mismatch",
                detail="configured image mismatch",
            )

    monkeypatch.setattr(
        "backend.app.project_workers.__main__.DockerIsolationBackend",
        UnavailableDocker,
    )
    with pytest.raises(RuntimeError, match="will not fall back"):
        build_runtime(
            database_path=tmp_path / "control.db",
            workspace_root=tmp_path,
            execution_backend="docker",
        )


def test_legacy_host_backend_is_retired_even_if_old_opt_in_is_set(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ASTRA_ALLOW_LEGACY_PROJECT_EXECUTION", "1")
    with pytest.raises(RuntimeError, match="has been retired"):
        build_runtime(
            database_path=tmp_path / "control.db",
            workspace_root=tmp_path,
            execution_backend="legacy",
        )


def test_dispatch_only_cli_runs_one_cycle(tmp_path: Path, capsys) -> None:
    result = main([
        "--once",
        "--dispatch-only",
        "--worker-id",
        "test-worker",
        "--database-path",
        str(tmp_path / "control.db"),
        "--workspace-root",
        str(tmp_path),
    ])

    assert result == 0
    output = capsys.readouterr().out
    assert '"worker_id": "test-worker"' in output
    assert '"executed": false' in output


def test_idle_polling_clamps_to_one_second_and_suppresses_empty_reports() -> None:
    assert clamp_idle_seconds(0.01) == 1.0
    assert clamp_idle_seconds(1.5) == 1.5
    assert clamp_idle_seconds(60) == 30.0
    assert report_has_activity({
        "executed": False,
        "dispatched_request_ids": [],
        "recovered_dispatch_ids": [],
        "deferred_dispatch_ids": [],
        "recovered_request_ids": [],
        "canonical_recovery_ids": [],
    }) is False
    assert report_has_activity({"executed": True}) is True
    assert report_has_activity({"dispatched_request_ids": ["request-1"]}) is True
