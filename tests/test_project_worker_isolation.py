from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.app.project_workers import (
    DockerIsolationBackend,
    IsolationProfile,
    ProjectExecutionPolicyError,
    WorkerCommandAction,
    WorkerLimits,
    build_execution_spec,
    create_workspace_snapshot,
)
from backend.app.project_workers.policy import PreparedExecution


DIGEST = "sha256:" + ("a" * 64)


def _profile() -> IsolationProfile:
    return IsolationProfile(
        profile_id="astra-python-node-v1",
        image_reference="astra-project-runtime:stage2c-v1",
        image_digest=DIGEST,
    )


def _runner_for_digest(digest: str = DIGEST):
    def run(command, **_kwargs):
        return subprocess.CompletedProcess(command, 0, stdout=digest + "\n", stderr="")
    return run


def _request():
    return SimpleNamespace(
        worker_request_id="worker-1",
        limits=WorkerLimits(
            lease_seconds=10,
            timeout_seconds=30,
            max_output_bytes=4096,
            memory_limit_mb=256,
        ),
    )


def _prepared(root: Path, *, action=WorkerCommandAction.PYTHON_SCRIPT):
    target = "check.py" if action == WorkerCommandAction.PYTHON_SCRIPT else None
    spec = build_execution_spec(
        action=action,
        command_id="command-1",
        target=target,
        image_digest=DIGEST,
    )
    return PreparedExecution(
        spec=spec,
        repository_root=root,
        working_directory=root,
        argv=("host-command-must-not-be-used",),
    )


def test_docker_launch_is_pinned_networkless_and_capability_free(tmp_path: Path) -> None:
    root = tmp_path / "source"
    snapshot = tmp_path / "snapshot"
    root.mkdir()
    snapshot.mkdir()
    (root / "check.py").write_text("print('ok')\n", encoding="utf-8")
    backend = DockerIsolationBackend(
        _profile(),
        docker_executable="/bin/true",
        command_runner=_runner_for_digest(),
    )

    command = backend.build_launch_command(
        _request(),
        _prepared(root),
        snapshot,
        "astra-worker-1",
    )
    joined = " ".join(command)
    assert command[:2] == ("/bin/true", "run")
    assert "--network none" in joined
    assert "--read-only" in command
    assert "--cap-drop ALL" in joined
    assert "--security-opt no-new-privileges" in joined
    assert "--user 65532:65532" in joined
    assert "--pids-limit 256" in joined
    assert "--memory 256m" in joined
    assert "--cpus 2" in joined
    assert "type=bind" in joined and "dst=/workspace,rw" in joined
    assert str(root) not in joined
    assert "DOCKER_HOST" not in joined
    assert command[-2:] == ("python3", "/workspace/check.py")


def test_image_digest_mismatch_fails_closed_without_launch(tmp_path: Path) -> None:
    root = tmp_path / "source"
    snapshot = tmp_path / "snapshot"
    root.mkdir()
    snapshot.mkdir()
    backend = DockerIsolationBackend(
        _profile(),
        docker_executable="/bin/true",
        command_runner=_runner_for_digest("sha256:" + ("b" * 64)),
    )
    capability = backend.probe()
    assert capability.available is False
    assert capability.failure_code == "image_digest_mismatch"
    with pytest.raises(ProjectExecutionPolicyError, match="pinned digest"):
        backend.build_launch_command(
            _request(),
            _prepared(root),
            snapshot,
            "astra-worker-1",
        )


def test_missing_docker_and_docker_ps_do_not_fall_back(tmp_path: Path) -> None:
    missing = DockerIsolationBackend(
        _profile(),
        docker_executable="definitely-not-an-astra-runtime",
    )
    assert missing.probe().failure_code == "docker_unavailable"

    root = tmp_path / "source"
    snapshot = tmp_path / "snapshot"
    root.mkdir()
    snapshot.mkdir()
    backend = DockerIsolationBackend(
        _profile(),
        docker_executable="/bin/true",
        command_runner=_runner_for_digest(),
    )
    with pytest.raises(ProjectExecutionPolicyError, match="docker_ps"):
        backend.build_launch_command(
            _request(),
            _prepared(root, action=WorkerCommandAction.DOCKER_PS),
            snapshot,
            "astra-worker-1",
        )


def test_historical_v1_execution_spec_is_read_only(tmp_path: Path) -> None:
    root = tmp_path / "source"
    snapshot = tmp_path / "snapshot"
    root.mkdir()
    snapshot.mkdir()
    prepared = _prepared(root)
    historical = PreparedExecution(
        spec=prepared.spec.model_copy(
            update={"schema_version": "astra.project-workers.execution-spec.v1"}
        ),
        repository_root=prepared.repository_root,
        working_directory=prepared.working_directory,
        argv=prepared.argv,
    )
    backend = DockerIsolationBackend(
        _profile(),
        docker_executable="/bin/true",
        command_runner=_runner_for_digest(),
    )

    with pytest.raises(ProjectExecutionPolicyError, match="Historical execution specifications"):
        backend.build_launch_command(
            _request(),
            historical,
            snapshot,
            "astra-worker-1",
        )


def test_workspace_snapshot_excludes_credentials_and_vcs_metadata(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    (root / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / ".env").write_text("TOKEN=secret\n", encoding="utf-8")
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("private\n", encoding="utf-8")
    snapshot = create_workspace_snapshot(root, tmp_path / "snapshot")
    assert (snapshot / "app.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert not (snapshot / ".env").exists()
    assert not (snapshot / ".git").exists()


def test_workspace_snapshot_rejects_symlinks_and_size_overflow(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")
    try:
        (root / "link.txt").symlink_to(outside)
    except OSError:
        pytest.skip("The test environment cannot create symlinks.")
    with pytest.raises(ProjectExecutionPolicyError, match="symlinks"):
        create_workspace_snapshot(root, tmp_path / "snapshot")

    (root / "link.txt").unlink()
    (root / "large.bin").write_bytes(b"x" * 20)
    with pytest.raises(ProjectExecutionPolicyError, match="bounded"):
        create_workspace_snapshot(
            root,
            tmp_path / "bounded-snapshot",
            max_bytes=10,
        )
