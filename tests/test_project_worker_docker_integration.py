from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.app.main import create_app
from backend.app.project_control.contracts import ExecutionAttemptType
from backend.app.project_workers import (
    DockerIsolationBackend,
    IsolationProfile,
    ProjectIsolatedExecutor,
    ProjectWorkerQueue,
    ProjectWorkerService,
    WorkerCommandAction,
    WorkerLimits,
    WorkerRequestStatus,
    build_execution_spec,
    container_identity_for,
    create_workspace_snapshot,
)
from backend.app.project_workers.policy import PreparedExecution
from tests.test_project_worker_execution import _runtime


pytestmark = pytest.mark.docker_integration
IMAGE_REFERENCE = "astra-project-runtime:stage2c-v1"
DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


@pytest.fixture(scope="session")
def docker_runtime() -> tuple[DockerIsolationBackend, str]:
    docker = shutil.which("docker")
    if docker is None:
        pytest.skip("Docker is unavailable.")
    info = subprocess.run(
        [docker, "info"],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if info.returncode != 0:
        pytest.skip("The Docker engine is unavailable.")
    image_reference = os.getenv("ASTRA_PROJECT_RUNTIME_IMAGE", IMAGE_REFERENCE)
    configured_digest = os.getenv("ASTRA_PROJECT_RUNTIME_IMAGE_DIGEST", "").lower()
    if not DIGEST_PATTERN.fullmatch(configured_digest):
        pytest.skip("ASTRA_PROJECT_RUNTIME_IMAGE_DIGEST is absent or invalid.")
    inspected = subprocess.run(
        [docker, "image", "inspect", image_reference, "--format", "{{.Id}}"],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if inspected.returncode != 0:
        pytest.skip(f"The expected image is unavailable: {image_reference}")
    profile = IsolationProfile(
        profile_id="astra-python-node-v1",
        image_reference=image_reference,
        image_digest=configured_digest,
    )
    backend = DockerIsolationBackend(profile)
    capability = backend.probe()
    assert capability.available is True, capability.detail
    assert capability.failure_code is None
    assert capability.observed_image_digest == configured_digest
    yield backend, configured_digest
    backend.cleanup_orphans(_preexisting_managed_containers(docker))


def _request(
    *,
    timeout_seconds: int = 10,
    max_output_bytes: int = 32_768,
    memory_limit_mb: int = 128,
) -> SimpleNamespace:
    return SimpleNamespace(
        worker_request_id=f"docker-integration-{uuid4().hex}",
        limits=WorkerLimits(
            lease_seconds=max(5, timeout_seconds + 2),
            timeout_seconds=timeout_seconds,
            max_output_bytes=max_output_bytes,
            memory_limit_mb=memory_limit_mb,
        ),
    )


def _prepared(
    source: Path,
    digest: str,
    *,
    action: WorkerCommandAction,
    target: str | None = None,
    arguments: tuple[str, ...] = (),
    working_directory: str = ".",
) -> PreparedExecution:
    spec = build_execution_spec(
        action=action,
        command_id=f"command-{uuid4().hex}",
        target=target,
        arguments=arguments,
        working_directory=working_directory,
        image_digest=digest,
    )
    workdir = source if working_directory == "." else source / working_directory
    return PreparedExecution(
        spec=spec,
        repository_root=source,
        working_directory=workdir,
        argv=("host-execution-is-forbidden",),
    )


def _execute(
    backend: DockerIsolationBackend,
    digest: str,
    source: Path,
    snapshot: Path,
    *,
    action: WorkerCommandAction,
    target: str | None = None,
    arguments: tuple[str, ...] = (),
    working_directory: str = ".",
    timeout_seconds: int = 10,
    max_output_bytes: int = 32_768,
):
    request = _request(
        timeout_seconds=timeout_seconds,
        max_output_bytes=max_output_bytes,
    )
    copied = create_workspace_snapshot(source, snapshot)
    result = backend.execute(
        request,
        _prepared(
            source,
            digest,
            action=action,
            target=target,
            arguments=arguments,
            working_directory=working_directory,
        ),
        copied,
        cancel_requested=lambda: False,
        heartbeat=lambda: None,
    )
    return request, result, copied


def _docker(docker_runtime) -> str:
    backend, _digest = docker_runtime
    return backend.docker_executable


def _remaining_container_ids(docker: str, name: str) -> list[str]:
    result = subprocess.run(
        [docker, "ps", "--all", "--filter", f"name=^{name}$", "--format", "{{.ID}}"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    return [item for item in result.stdout.splitlines() if item.strip()]


def _preexisting_managed_containers(docker: str) -> tuple[str, ...]:
    result = subprocess.run(
        [
            docker,
            "ps",
            "--all",
            "--filter",
            "label=astra.worker.managed=true",
            "--format",
            "{{.Names}}",
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    return tuple(item for item in result.stdout.splitlines() if item.strip())


def test_real_image_probe_and_fail_closed_variants(docker_runtime) -> None:
    backend, digest = docker_runtime
    capability = backend.probe()
    assert capability.available is True
    assert capability.observed_image_digest == digest
    assert {item.value for item in capability.supported_toolchains} == {"python", "node"}

    wrong = DockerIsolationBackend(backend.profile.model_copy(
        update={"image_digest": "sha256:" + ("b" * 64)}
    ))
    assert wrong.probe().failure_code == "image_digest_mismatch"

    missing = DockerIsolationBackend(backend.profile.model_copy(
        update={"image_reference": "astra-project-runtime:definitely-missing"}
    ))
    assert missing.probe().failure_code == "image_unavailable"

    unavailable = DockerIsolationBackend(
        backend.profile,
        docker_executable="definitely-not-an-astra-docker-client",
    )
    assert unavailable.probe().failure_code == "docker_unavailable"

    with pytest.raises(ValidationError, match="image_digest"):
        IsolationProfile(
            profile_id="astra-python-node-v1",
            image_reference=IMAGE_REFERENCE,
            image_digest="sha256:malformed",
        )


def test_real_container_identity_network_filesystem_and_security(
    tmp_path: Path,
    docker_runtime,
) -> None:
    backend, digest = docker_runtime
    source = tmp_path / "source"
    source.mkdir()
    marker = source / "marker.txt"
    marker.write_text("real-repository-original\n", encoding="utf-8")
    for relative, value in {
        ".env": "TOKEN=host-secret\n",
        ".npmrc": "//registry/:_authToken=host-secret\n",
        "credentials.json": '{"token":"host-secret"}\n',
        "private.pem": "PRIVATE KEY\n",
    }.items():
        (source / relative).write_text(value, encoding="utf-8")
    (source / ".git").mkdir()
    (source / ".git" / "config").write_text("private\n", encoding="utf-8")
    (source / ".ssh").mkdir()
    (source / ".ssh" / "id_rsa").write_text("private\n", encoding="utf-8")
    probe = source / "probe.py"
    probe.write_text(
        """
import json
import os
import socket
from pathlib import Path

status = {}
for line in Path('/proc/self/status').read_text().splitlines():
    if line.startswith(('NoNewPrivs:', 'CapEff:')):
        key, value = line.split(':', 1)
        status[key] = value.strip()

def read_optional(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None

def read_first(paths):
    for path in paths:
        value = read_optional(path)
        if value is not None:
            return value
    return None

tcp_blocked = False
sock = socket.socket()
sock.settimeout(0.5)
try:
    sock.connect(('1.1.1.1', 53))
except OSError:
    tcp_blocked = True
finally:
    sock.close()

dns_blocked = False
try:
    socket.getaddrinfo('example.com', 443)
except OSError:
    dns_blocked = True

root_write_blocked = False
try:
    Path('/astra-root-write').write_text('no')
except OSError:
    root_write_blocked = True

Path('/tmp/astra-write').write_text('ok')
Path('/home/astra/astra-write').write_text('ok')
Path('/workspace/marker.txt').write_text('snapshot-only\\n')
sensitive_names = ['.git', '.hg', '.svn', '.ssh', '.env', '.npmrc', 'credentials.json', 'private.pem']
leaked_environment = sorted(
    key for key in os.environ
    if any(part in key.lower() for part in ('proxy', 'token', 'secret', 'password', 'access_key'))
)
print(json.dumps({
    'uid': os.getuid(),
    'gid': os.getgid(),
    'no_new_privileges': status.get('NoNewPrivs'),
    'cap_eff': status.get('CapEff'),
    'root_read_only': bool(os.statvfs('/').f_flag & os.ST_RDONLY),
    'root_write_blocked': root_write_blocked,
    'tmp_writable': Path('/tmp/astra-write').read_text() == 'ok',
    'home_writable': Path('/home/astra/astra-write').read_text() == 'ok',
    'workspace_writable': Path('/workspace/marker.txt').read_text() == 'snapshot-only\\n',
    'tcp_blocked': tcp_blocked,
    'dns_blocked': dns_blocked,
    'docker_socket_absent': not Path('/var/run/docker.sock').exists(),
    'sensitive_paths_absent': all(not Path('/workspace', name).exists() for name in sensitive_names),
    'leaked_environment': leaked_environment,
    'pids_max': read_first(('/sys/fs/cgroup/pids.max', '/sys/fs/cgroup/pids/pids.max')),
    'memory_max': read_first(('/sys/fs/cgroup/memory.max', '/sys/fs/cgroup/memory/memory.limit_in_bytes')),
    'cpu_max': read_first(('/sys/fs/cgroup/cpu.max', '/sys/fs/cgroup/cpu/cpu.cfs_quota_us')),
    'cpu_period': read_optional('/sys/fs/cgroup/cpu/cpu.cfs_period_us'),
}, sort_keys=True))
""".lstrip(),
        encoding="utf-8",
    )

    _request_value, result, snapshot = _execute(
        backend,
        digest,
        source,
        tmp_path / "snapshot",
        action=WorkerCommandAction.PYTHON_SCRIPT,
        target="probe.py",
    )
    assert result.outcome == "succeeded", result.stderr
    evidence = json.loads(result.stdout.strip().splitlines()[-1])
    assert evidence["uid"] == 65532
    assert evidence["gid"] == 65532
    assert evidence["no_new_privileges"] == "1"
    assert int(evidence["cap_eff"], 16) == 0
    assert evidence["root_read_only"] is True
    assert evidence["root_write_blocked"] is True
    assert evidence["tmp_writable"] is True
    assert evidence["home_writable"] is True
    assert evidence["workspace_writable"] is True
    assert evidence["tcp_blocked"] is True
    assert evidence["dns_blocked"] is True
    assert evidence["docker_socket_absent"] is True
    assert evidence["sensitive_paths_absent"] is True
    assert evidence["leaked_environment"] == []
    assert evidence["pids_max"] == str(backend.profile.pids_limit)
    assert int(evidence["memory_max"]) == 128 * 1024 * 1024
    cpu_items = evidence["cpu_max"].split()
    quota = int(cpu_items[0])
    period = int(cpu_items[1]) if len(cpu_items) == 2 else int(evidence["cpu_period"])
    assert quota / period == backend.profile.cpu_limit
    assert marker.read_text(encoding="utf-8") == "real-repository-original\n"
    assert (snapshot / "marker.txt").read_text(encoding="utf-8") == "snapshot-only\n"


def test_real_launch_applies_docker_resource_and_mount_policy(
    tmp_path: Path,
    docker_runtime,
) -> None:
    backend, digest = docker_runtime
    docker = _docker(docker_runtime)
    source = tmp_path / "source"
    source.mkdir()
    (source / "sleep.py").write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
    snapshot = create_workspace_snapshot(source, tmp_path / "snapshot")
    request = _request(timeout_seconds=40, memory_limit_mb=96)
    identity = container_identity_for(request.worker_request_id)
    command = backend.build_launch_command(
        request,
        _prepared(
            source,
            digest,
            action=WorkerCommandAction.PYTHON_SCRIPT,
            target="sleep.py",
        ),
        snapshot,
        identity,
    )
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 10
        while not _remaining_container_ids(docker, identity):
            assert process.poll() is None
            assert time.monotonic() < deadline
            time.sleep(0.05)
        inspected = subprocess.run(
            [docker, "inspect", identity],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        container = json.loads(inspected.stdout)[0]
        host = container["HostConfig"]
        assert host["NetworkMode"] == "none"
        assert host["ReadonlyRootfs"] is True
        assert host["CapDrop"] == ["ALL"]
        assert "no-new-privileges" in host["SecurityOpt"]
        assert host["PidsLimit"] == backend.profile.pids_limit
        assert host["Memory"] == 96 * 1024 * 1024
        assert host["NanoCpus"] == int(backend.profile.cpu_limit * 1_000_000_000)
        assert container["Config"]["User"] == "65532:65532"
        workspace_mount = next(
            item for item in container["Mounts"] if item["Destination"] == "/workspace"
        )
        assert Path(workspace_mount["Source"]).resolve() == snapshot.resolve()
        assert Path(workspace_mount["Source"]).resolve() != source.resolve()
        assert workspace_mount["RW"] is True
        assert not any(item["Destination"] == "/var/run/docker.sock" for item in container["Mounts"])
    finally:
        backend.cancel(identity)
        process.communicate(timeout=10)
    assert _remaining_container_ids(docker, identity) == []


def test_real_pid_limit_bounds_excessive_child_creation(
    tmp_path: Path,
    docker_runtime,
) -> None:
    backend, digest = docker_runtime
    limited = DockerIsolationBackend(
        backend.profile.model_copy(update={"pids_limit": 32})
    )
    source = tmp_path / "pid-limit"
    source.mkdir()
    (source / "fork_probe.py").write_text(
        """
import json
import subprocess

children = []
blocked = False
for _ in range(80):
    try:
        children.append(subprocess.Popen(['/bin/sleep', '5']))
    except OSError:
        blocked = True
        break
print(json.dumps({'created': len(children), 'blocked': blocked}))
for child in children:
    child.terminate()
for child in children:
    child.wait()
""".lstrip(),
        encoding="utf-8",
    )
    _request_value, result, _snapshot = _execute(
        limited,
        digest,
        source,
        tmp_path / "pid-limit-snapshot",
        action=WorkerCommandAction.PYTHON_SCRIPT,
        target="fork_probe.py",
    )
    assert result.outcome == "succeeded", result.stderr
    evidence = json.loads(result.stdout.strip().splitlines()[-1])
    assert evidence["blocked"] is True
    assert evidence["created"] < 32


@pytest.mark.parametrize(
    ("case_name", "action", "target", "files", "expected"),
    [
        (
            "python-pytest-pass",
            WorkerCommandAction.PYTEST,
            None,
            {"test_sample.py": "def test_ok():\n    assert True\n"},
            "succeeded",
        ),
        (
            "python-pytest-fail",
            WorkerCommandAction.PYTEST,
            None,
            {"test_sample.py": "def test_bad():\n    assert False\n"},
            "failed",
        ),
        (
            "python-script-pass",
            WorkerCommandAction.PYTHON_SCRIPT,
            "run.py",
            {"run.py": "print('python-ok')\n"},
            "succeeded",
        ),
        (
            "python-script-exception",
            WorkerCommandAction.PYTHON_SCRIPT,
            "run.py",
            {"run.py": "raise RuntimeError('expected')\n"},
            "failed",
        ),
        (
            "node-test-pass",
            WorkerCommandAction.NODE_TEST,
            "sample.test.js",
            {"sample.test.js": "const test=require('node:test');test('ok',()=>{});\n"},
            "succeeded",
        ),
        (
            "node-test-fail",
            WorkerCommandAction.NODE_TEST,
            "sample.test.js",
            {"sample.test.js": "const test=require('node:test');test('bad',()=>{throw Error('x')});\n"},
            "failed",
        ),
        (
            "npm-test-pass",
            WorkerCommandAction.NPM_TEST,
            None,
            {
                "package.json": '{"scripts":{"test":"node --test"}}\n',
                "sample.test.js": "const test=require('node:test');test('ok',()=>{});\n",
            },
            "succeeded",
        ),
        (
            "npm-test-fail",
            WorkerCommandAction.NPM_TEST,
            None,
            {
                "package.json": '{"scripts":{"test":"node --test"}}\n',
                "sample.test.js": "const test=require('node:test');test('bad',()=>{throw Error('x')});\n",
            },
            "failed",
        ),
    ],
)
def test_python_and_node_smoke_flows(
    tmp_path: Path,
    docker_runtime,
    case_name: str,
    action: WorkerCommandAction,
    target: str | None,
    files: dict[str, str],
    expected: str,
) -> None:
    backend, digest = docker_runtime
    source = tmp_path / case_name
    source.mkdir()
    for relative, content in files.items():
        (source / relative).write_text(content, encoding="utf-8")
    request, result, _snapshot = _execute(
        backend,
        digest,
        source,
        tmp_path / f"{case_name}-snapshot",
        action=action,
        target=target,
        arguments=("-q",) if action == WorkerCommandAction.PYTEST else (),
    )
    assert result.outcome == expected, result.stderr
    assert _remaining_container_ids(_docker(docker_runtime), result.container_identity) == []
    assert result.container_identity == container_identity_for(request.worker_request_id)


def test_real_timeout_is_terminal_and_removes_container(
    tmp_path: Path,
    docker_runtime,
) -> None:
    backend, digest = docker_runtime
    source = tmp_path / "timeout"
    source.mkdir()
    (source / "sleep.py").write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
    request, result, _snapshot = _execute(
        backend,
        digest,
        source,
        tmp_path / "timeout-snapshot",
        action=WorkerCommandAction.PYTHON_SCRIPT,
        target="sleep.py",
        timeout_seconds=1,
    )
    assert result.outcome == "timed_out"
    assert result.timed_out is True
    assert result.cancelled is False
    assert _remaining_container_ids(
        _docker(docker_runtime),
        container_identity_for(request.worker_request_id),
    ) == []


def test_real_output_is_bounded_and_redacted_before_persistence(
    tmp_path: Path,
    docker_runtime,
) -> None:
    backend, digest = docker_runtime
    _root, _script, _control, queue, service, request, _legacy = _runtime(
        tmp_path,
        "print('api_key=supersecret')\n"
        "print('sk-abcdefghijklmnop')\n"
        "print('x' * 20000)\n",
        limits=WorkerLimits(
            lease_seconds=10,
            timeout_seconds=5,
            max_output_bytes=4096,
            memory_limit_mb=128,
        ),
        image_digest=digest,
        through_outbox=True,
    )
    executor = ProjectIsolatedExecutor(service, backend, tmp_path / "evidence")
    assert executor.run_once("real-output-worker") is True
    finished = queue.get(request.worker_request_id)
    output = finished.result_reference["stdout_excerpt"]
    assert finished.status == WorkerRequestStatus.SUCCEEDED
    assert finished.result_reference["output_truncated"] is True
    assert "supersecret" not in output
    assert "sk-abcdefghijklmnop" not in output
    assert "<redacted>" in output
    assert "<redacted-api-key>" in output


def test_real_cancellation_has_one_request_and_no_restart_reexecution(
    tmp_path: Path,
    docker_runtime,
) -> None:
    backend, digest = docker_runtime
    _root, _script, control, queue, service, request, _legacy = _runtime(
        tmp_path,
        "import time\ntime.sleep(30)\n",
        limits=WorkerLimits(
            lease_seconds=15,
            timeout_seconds=40,
            max_output_bytes=4096,
            memory_limit_mb=128,
        ),
        image_digest=digest,
        through_outbox=True,
    )
    executor = ProjectIsolatedExecutor(service, backend, tmp_path / "evidence")
    thread = threading.Thread(
        target=executor.run_once,
        args=("real-cancel-worker",),
        daemon=True,
    )
    thread.start()
    deadline = time.monotonic() + 10
    while queue.get(request.worker_request_id).status == WorkerRequestStatus.QUEUED:
        assert time.monotonic() < deadline
        time.sleep(0.05)
    service.request_cancel(request.worker_request_id)
    thread.join(timeout=10)
    assert not thread.is_alive()
    finished = queue.get(request.worker_request_id)
    assert finished.status == WorkerRequestStatus.CANCELLED
    assert finished.canonical_reconciled_at is not None

    restarted_queue = ProjectWorkerQueue(queue.database_path)
    restarted_queue.initialize()
    restarted = ProjectWorkerService(control, restarted_queue)
    restarted.recover_expired_leases()
    assert ProjectIsolatedExecutor(
        restarted,
        backend,
        tmp_path / "restarted-evidence",
    ).run_once("restart-worker") is False
    with sqlite3.connect(queue.database_path) as connection:
        request_count = connection.execute(
            "SELECT COUNT(*) FROM project_worker_requests WHERE worker_request_id = ?",
            (request.worker_request_id,),
        ).fetchone()[0]
        terminal_count = connection.execute(
            "SELECT COUNT(*) FROM project_worker_events "
            "WHERE worker_request_id = ? AND event_type = 'cancelled'",
            (request.worker_request_id,),
        ).fetchone()[0]
    assert request_count == 1
    assert terminal_count == 1
    assert _remaining_container_ids(
        _docker(docker_runtime),
        container_identity_for(request.worker_request_id),
    ) == []


def test_real_success_recovers_after_queue_completion_before_reconciliation(
    tmp_path: Path,
    docker_runtime,
    monkeypatch,
) -> None:
    backend, digest = docker_runtime
    _root, _script, control, queue, service, request, _legacy = _runtime(
        tmp_path,
        "print('real-success')\n",
        limits=WorkerLimits(
            lease_seconds=10,
            timeout_seconds=5,
            max_output_bytes=4096,
            memory_limit_mb=128,
        ),
        image_digest=digest,
        through_outbox=True,
    )
    monkeypatch.setattr(service, "_reconcile_terminal", lambda _request: False)
    executor = ProjectIsolatedExecutor(service, backend, tmp_path / "evidence")
    assert executor.run_once("real-success-worker") is True
    terminal = queue.get(request.worker_request_id)
    assert terminal.status == WorkerRequestStatus.SUCCEEDED
    assert terminal.canonical_reconciled_at is None

    restarted_queue = ProjectWorkerQueue(queue.database_path)
    restarted_queue.initialize()
    restarted = ProjectWorkerService(control, restarted_queue)
    assert restarted.dispatch_pending().dispatched_request_ids == ()
    recovery = restarted.recover_expired_leases()
    assert recovery.canonical_recovery_ids == (request.worker_request_id,)
    recovered = restarted_queue.get(request.worker_request_id)
    assert recovered.canonical_reconciled_at is not None
    assert ProjectIsolatedExecutor(
        restarted,
        backend,
        tmp_path / "restart-evidence",
    ).run_once("restart-worker") is False

    command_attempts = [
        item
        for item in control.list_attempts(request.project_run_id)
        if item.attempt_type == ExecutionAttemptType.COMMAND
    ]
    dispatches = control.list_execution_dispatches(request.project_run_id)
    with sqlite3.connect(queue.database_path) as connection:
        request_count = connection.execute(
            "SELECT COUNT(*) FROM project_worker_requests WHERE worker_request_id = ?",
            (request.worker_request_id,),
        ).fetchone()[0]
    assert len(command_attempts) == 1
    assert len(dispatches) == 1
    assert request_count == 1
    assert _remaining_container_ids(
        _docker(docker_runtime),
        container_identity_for(request.worker_request_id),
    ) == []


def test_orphan_cleanup_preserves_exact_active_set(docker_runtime) -> None:
    backend, _digest = docker_runtime
    docker = _docker(docker_runtime)
    preexisting = _preexisting_managed_containers(docker)
    orphan = f"astra-integration-orphan-{uuid4().hex[:10]}"
    active = f"astra-integration-active-{uuid4().hex[:10]}"
    for name in (orphan, active):
        subprocess.run(
            [
                docker,
                "run",
                "--detach",
                "--name",
                name,
                "--label",
                "astra.worker.managed=true",
                backend.profile.image_reference,
                "python3",
                "-c",
                "import time; time.sleep(30)",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
    try:
        removed = backend.cleanup_orphans((*preexisting, active))
        assert orphan in removed
        assert _remaining_container_ids(docker, orphan) == []
        assert _remaining_container_ids(docker, active)
    finally:
        backend.cancel(orphan)
        backend.cancel(active)


def test_runtime_capability_endpoint_reports_real_docker_and_worker(
    tmp_path: Path,
    docker_runtime,
    monkeypatch,
) -> None:
    backend, digest = docker_runtime
    monkeypatch.setenv("ASTRA_PROJECT_EXECUTION_BACKEND", "docker")
    monkeypatch.setenv("ASTRA_PROJECT_RUNTIME_IMAGE", backend.profile.image_reference)
    monkeypatch.setenv("ASTRA_PROJECT_RUNTIME_IMAGE_DIGEST", digest)
    app = create_app(tmp_path / "app.db", workspace_root=tmp_path)
    with TestClient(app) as client:
        app.state.project_worker_service.record_runtime_heartbeat(
            "real-docker-worker",
            execution_backend="docker",
            supported_attempt_types=(
                ExecutionAttemptType.COMMAND,
                ExecutionAttemptType.VERIFICATION,
            ),
            supported_toolchains=("python", "node"),
        )
        response = client.get("/chat/projects/runtime-capabilities")
    assert response.status_code == 200
    payload = response.json()
    assert payload["execution_backend"] == "docker"
    assert payload["worker_available"] is True
    assert payload["host_execution_fallback"] is False
    assert payload["isolation_capability"]["available"] is True
    assert payload["isolation_capability"]["configured_image_digest"] == digest
    assert payload["isolation_capability"]["observed_image_digest"] == digest
    assert set(payload["isolation_capability"]["supported_toolchains"]) == {
        "python",
        "node",
    }
