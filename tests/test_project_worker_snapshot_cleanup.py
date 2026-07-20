from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from backend.app.project_workers import (
    DockerIsolationBackend,
    IsolationProfile,
    WorkerCommandAction,
)
from tests.test_project_worker_docker_integration import _execute, docker_runtime


DIGEST = "sha256:" + ("a" * 64)


def test_snapshot_cleanup_helper_is_non_root_and_fully_constrained(tmp_path: Path) -> None:
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    commands: list[list[str]] = []

    def runner(command, **_kwargs):
        commands.append(list(command))
        stdout = DIGEST + "\n" if command[1:3] == ["image", "inspect"] else ""
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    backend = DockerIsolationBackend(
        IsolationProfile(
            profile_id="astra-python-node-v1",
            image_reference="astra-project-runtime:stage2c-v1",
            image_digest=DIGEST,
        ),
        docker_executable="/bin/true",
        command_runner=runner,
    )
    backend.prepare_snapshot_cleanup(snapshot)

    cleanup = commands[-1]
    joined = " ".join(cleanup)
    assert cleanup[:2] == ["/bin/true", "run"]
    assert "--network none" in joined
    assert "--read-only" in cleanup
    assert "--cap-drop ALL" in joined
    assert "--security-opt no-new-privileges" in joined
    assert "--user 65532:65532" in joined
    assert "--pids-limit 16" in joined
    assert "--memory 64m" in joined
    assert "--cpus 0.25" in joined
    assert "--entrypoint /usr/bin/find" in joined
    assert cleanup[-8:] == [
        "/workspace", "-user", "65532", "-exec", "/bin/chmod", "a+rwX", "{}", "+",
    ]


@pytest.mark.docker_integration
def test_real_snapshot_cleanup_handles_container_owned_private_directories(
    tmp_path: Path,
    docker_runtime,
) -> None:
    backend, digest = docker_runtime
    source = tmp_path / "cleanup-project"
    source.mkdir()
    (source / "lock.py").write_text(
        "from pathlib import Path\n"
        "cache = Path('locked-cache')\n"
        "cache.mkdir(mode=0o700)\n"
        "(cache / 'nodeids').write_text('[]', encoding='utf-8')\n"
        "cache.chmod(0o700)\n",
        encoding="utf-8",
    )
    _request_record, result, copied = _execute(
        backend,
        digest,
        source,
        tmp_path / "cleanup-snapshot",
        action=WorkerCommandAction.PYTHON_SCRIPT,
        target="lock.py",
    )
    assert result.outcome == "succeeded"

    backend.prepare_snapshot_cleanup(copied)
    shutil.rmtree(copied)
    assert not copied.exists()
