from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import threading
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import BinaryIO, Protocol

from backend.app.project_workers.contracts import ProjectWorkerRequest
from backend.app.project_workers.policy import PreparedExecution, ProjectExecutionPolicyError
from backend.app.project_workers.runtime_contracts import (
    WORKER_EXECUTION_SPEC_VERSION,
    IsolationBackendKind,
    IsolationCapability,
    IsolationExecutionResult,
    IsolationProfile,
    IsolationToolchain,
    WorkerCommandAction,
)


_CONTAINER_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$")
_SUPPORTED_ACTIONS = (
    WorkerCommandAction.PYTEST,
    WorkerCommandAction.PYTHON_SCRIPT,
    WorkerCommandAction.NPM_TEST,
    WorkerCommandAction.NPM_RUN_LINT,
    WorkerCommandAction.NPM_RUN_BUILD,
    WorkerCommandAction.NPM_RUN_TYPECHECK,
    WorkerCommandAction.NODE_TEST,
)
_EXCLUDED_NAMES = frozenset({
    ".aws", ".azure", ".docker", ".git", ".hg", ".ssh", ".svn", ".venv",
    ".netrc", ".npmrc", ".pypirc", "credentials", "credentials.json",
    "id_ed25519", "id_rsa", "node_modules", "pip.conf", "__pycache__",
})
_EXCLUDED_SUFFIXES = frozenset({".key", ".pem", ".p12", ".pfx"})


class IsolationBackend(Protocol):
    def probe(self) -> IsolationCapability: ...

    def execute(
        self,
        request: ProjectWorkerRequest,
        prepared: PreparedExecution,
        workspace_snapshot: Path,
        *,
        cancel_requested: Callable[[], bool],
        heartbeat: Callable[[], None],
    ) -> IsolationExecutionResult: ...

    def cancel(self, container_identity: str) -> bool: ...

    def prepare_snapshot_cleanup(self, workspace_snapshot: Path) -> None: ...

    def cleanup_orphans(
        self,
        active_container_identities: Iterable[str] = (),
    ) -> tuple[str, ...]: ...


class DockerIsolationBackend:
    """Runs exact approved project code in a disposable Docker snapshot."""

    def __init__(
        self,
        profile: IsolationProfile,
        *,
        docker_executable: str | None = None,
        command_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        popen_factory: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
        poll_interval_seconds: float = 0.05,
    ) -> None:
        if profile.backend != IsolationBackendKind.DOCKER:
            raise ValueError("DockerIsolationBackend requires a Docker profile.")
        self.profile = profile
        self.docker_executable = docker_executable or shutil.which("docker") or "docker"
        self._run_command = command_runner
        self._popen = popen_factory
        self.poll_interval_seconds = max(0.01, min(float(poll_interval_seconds), 1.0))

    def probe(self) -> IsolationCapability:
        executable = (
            self.docker_executable
            if Path(self.docker_executable).is_file()
            else shutil.which(self.docker_executable)
        )
        if not executable:
            return self._capability(
                False,
                "docker_unavailable",
                "Docker is not installed or is not on PATH.",
            )
        try:
            result = self._run_command(
                [
                    self.docker_executable,
                    "image",
                    "inspect",
                    self.profile.image_reference,
                    "--format",
                    "{{.Id}}",
                ],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as error:
            return self._capability(
                False,
                "docker_unavailable",
                _bounded(str(error), 300),
            )
        if result.returncode != 0:
            return self._capability(
                False,
                "image_unavailable",
                _bounded(result.stderr or result.stdout, 300),
            )
        observed = result.stdout.strip().lower()
        if observed != self.profile.image_digest:
            return self._capability(
                False,
                "image_digest_mismatch",
                "The configured runtime image does not match its pinned digest.",
                observed=observed or None,
            )
        return self._capability(True, observed=observed)

    def build_launch_command(
        self,
        request: ProjectWorkerRequest,
        prepared: PreparedExecution,
        workspace_snapshot: Path,
        container_identity: str,
    ) -> tuple[str, ...]:
        capability = self.probe()
        if not capability.available:
            raise ProjectExecutionPolicyError(
                capability.detail or "Docker isolation is unavailable."
            )
        if not _CONTAINER_NAME_RE.fullmatch(container_identity):
            raise ProjectExecutionPolicyError("The container identity is invalid.")
        spec = prepared.spec
        if spec.schema_version != WORKER_EXECUTION_SPEC_VERSION:
            raise ProjectExecutionPolicyError(
                "Historical execution specifications cannot start new isolated work."
            )
        if spec.action == WorkerCommandAction.DOCKER_PS:
            raise ProjectExecutionPolicyError(
                "docker_ps is not an isolated project execution action."
            )
        if spec.action not in _SUPPORTED_ACTIONS:
            raise ProjectExecutionPolicyError(
                "The requested action is not supported by the isolation profile."
            )
        if spec.isolation_profile_id != self.profile.profile_id:
            raise ProjectExecutionPolicyError(
                "The execution specification is bound to a different isolation profile."
            )
        if spec.image_digest != self.profile.image_digest:
            raise ProjectExecutionPolicyError(
                "The execution specification is bound to a different runtime image digest."
            )
        if spec.network_mode.value != "none" or spec.filesystem_mode.value != "snapshot":
            raise ProjectExecutionPolicyError(
                "The execution specification requests an unsupported isolation mode."
            )
        snapshot = workspace_snapshot.resolve(strict=True)
        if not snapshot.is_dir() or "," in str(snapshot):
            raise ProjectExecutionPolicyError(
                "The isolated workspace snapshot is missing or cannot be mounted safely."
            )
        try:
            relative_workdir = prepared.working_directory.relative_to(
                prepared.repository_root
            ).as_posix()
        except ValueError as error:
            raise ProjectExecutionPolicyError(
                "The approved working directory escaped the repository."
            ) from error
        container_workdir = (
            "/workspace"
            if relative_workdir == "."
            else f"/workspace/{relative_workdir}"
        )
        memory_mb = request.limits.memory_limit_mb or 1024
        return tuple([
            self.docker_executable,
            "run",
            "--rm",
            "--name",
            container_identity,
            "--label",
            "astra.worker.managed=true",
            "--label",
            f"astra.worker.request_id={request.worker_request_id}",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            str(self.profile.pids_limit),
            "--memory",
            f"{memory_mb}m",
            "--cpus",
            _format_float(self.profile.cpu_limit),
            "--user",
            self.profile.run_as_user,
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,nodev,size=64m,mode=1777",
            "--tmpfs",
            (
                "/home/astra:rw,noexec,nosuid,nodev,size=32m,"
                "uid=65532,gid=65532,mode=0700"
            ),
            "--mount",
            f"type=bind,src={snapshot},dst=/workspace",
            "--workdir",
            container_workdir,
            "--env",
            "HOME=/home/astra",
            "--env",
            "TMPDIR=/tmp",
            "--env",
            "PYTHONDONTWRITEBYTECODE=1",
            "--env",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1",
            self.profile.image_reference,
            *_container_argv(prepared),
        ])

    def execute(
        self,
        request: ProjectWorkerRequest,
        prepared: PreparedExecution,
        workspace_snapshot: Path,
        *,
        cancel_requested: Callable[[], bool],
        heartbeat: Callable[[], None],
    ) -> IsolationExecutionResult:
        container_identity = container_identity_for(request.worker_request_id)
        command = self.build_launch_command(
            request,
            prepared,
            workspace_snapshot,
            container_identity,
        )
        started = time.monotonic()
        stream_limit = max(512, (request.limits.max_output_bytes - 1024) // 2)
        stdout_buffer = _BoundedBytes(stream_limit)
        stderr_buffer = _BoundedBytes(stream_limit)
        process = self._popen(
            list(command),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_docker_client_environment(),
            start_new_session=os.name != "nt",
        )
        stdout_thread = threading.Thread(
            target=_drain,
            args=(process.stdout, stdout_buffer),
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=_drain,
            args=(process.stderr, stderr_buffer),
            daemon=True,
        )
        stdout_thread.start()
        stderr_thread.start()
        cancelled = False
        timed_out = False
        heartbeat_interval = max(
            1.0,
            min(5.0, request.limits.lease_seconds / 3),
        )
        next_heartbeat = time.monotonic() + heartbeat_interval
        deadline = started + request.limits.timeout_seconds
        while process.poll() is None:
            now = time.monotonic()
            if cancel_requested():
                cancelled = True
                self.cancel(container_identity)
                break
            if now >= deadline:
                timed_out = True
                self.cancel(container_identity)
                break
            if now >= next_heartbeat:
                heartbeat()
                next_heartbeat = now + heartbeat_interval
            time.sleep(self.poll_interval_seconds)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.cancel(container_identity)
            process.kill()
            process.wait(timeout=5)
        stdout_thread.join(timeout=2)
        stderr_thread.join(timeout=2)
        duration_ms = max(0, int((time.monotonic() - started) * 1000))
        exit_code = process.returncode
        infrastructure_failure = bool(
            exit_code in {125, 126, 127} and not timed_out and not cancelled
        )
        if cancelled:
            outcome = "cancelled"
        elif timed_out:
            outcome = "timed_out"
        elif infrastructure_failure:
            outcome = "infrastructure_failed"
        elif exit_code in prepared.spec.expected_exit_codes:
            outcome = "succeeded"
        else:
            outcome = "failed"
        relative_workdir = prepared.working_directory.relative_to(
            prepared.repository_root
        ).as_posix()
        return IsolationExecutionResult(
            backend=IsolationBackendKind.DOCKER,
            profile_id=self.profile.profile_id,
            container_identity=container_identity,
            image_reference=self.profile.image_reference,
            image_digest=self.profile.image_digest,
            argv_display=tuple(_container_argv(prepared)),
            working_directory=relative_workdir,
            outcome=outcome,
            exit_code=exit_code,
            stdout=stdout_buffer.text(),
            stderr=stderr_buffer.text(),
            output_truncated=stdout_buffer.truncated or stderr_buffer.truncated,
            timed_out=timed_out,
            cancelled=cancelled,
            infrastructure_failure=infrastructure_failure,
            duration_ms=duration_ms,
            effective_policy={
                "network": "none",
                "filesystem": "snapshot",
                "root_filesystem_read_only": True,
                "capabilities": "none",
                "no_new_privileges": True,
                "pids_limit": self.profile.pids_limit,
                "memory_limit_mb": request.limits.memory_limit_mb or 1024,
                "cpu_limit": self.profile.cpu_limit,
            },
        )

    def prepare_snapshot_cleanup(self, workspace_snapshot: Path) -> None:
        """Restore host-removable permissions without executing as root.

        Project tools can create mode-0700 cache directories owned by the fixed
        container UID. A second constrained container running as that same UID
        makes only the disposable snapshot removable before host-side deletion.
        """
        capability = self.probe()
        if not capability.available:
            raise ProjectExecutionPolicyError(
                capability.detail or "Docker isolation is unavailable for snapshot cleanup."
            )
        snapshot = workspace_snapshot.resolve(strict=True)
        if not snapshot.is_dir() or "," in str(snapshot):
            raise ProjectExecutionPolicyError(
                "The isolated workspace snapshot cannot be cleaned safely."
            )
        cleanup_identity = (
            "astra-cleanup-"
            + hashlib.sha256(str(snapshot).encode("utf-8")).hexdigest()[:24]
        )
        command = [
            self.docker_executable,
            "run",
            "--rm",
            "--name",
            cleanup_identity,
            "--label",
            "astra.worker.managed=true",
            "--label",
            "astra.worker.operation=snapshot-cleanup",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            "16",
            "--memory",
            "64m",
            "--cpus",
            "0.25",
            "--user",
            self.profile.run_as_user,
            "--mount",
            f"type=bind,src={snapshot},dst=/workspace",
            "--workdir",
            "/workspace",
            "--entrypoint",
            "/usr/bin/find",
            self.profile.image_reference,
            "/workspace",
            "-user",
            "65532",
            "-exec",
            "/bin/chmod",
            "a+rwX",
            "{}",
            "+",
        ]
        try:
            result = self._run_command(
                command,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as error:
            self.cancel(cleanup_identity)
            raise ProjectExecutionPolicyError(
                f"The disposable snapshot cleanup helper failed: {_bounded(str(error), 300)}"
            ) from error
        if result.returncode != 0:
            self.cancel(cleanup_identity)
            detail = _bounded(result.stderr or result.stdout, 300)
            raise ProjectExecutionPolicyError(
                f"The disposable snapshot permissions could not be restored: {detail}"
            )

    def cancel(self, container_identity: str) -> bool:
        if not _CONTAINER_NAME_RE.fullmatch(container_identity):
            return False
        try:
            result = self._run_command(
                [self.docker_executable, "rm", "--force", container_identity],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return result.returncode == 0

    def cleanup_orphans(
        self,
        active_container_identities: Iterable[str] = (),
    ) -> tuple[str, ...]:
        active = set(active_container_identities)
        try:
            listed = self._run_command(
                [
                    self.docker_executable,
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
        except (OSError, subprocess.SubprocessError):
            return ()
        if listed.returncode != 0:
            return ()
        removed: list[str] = []
        for name in sorted(set(listed.stdout.splitlines()) - active):
            if _CONTAINER_NAME_RE.fullmatch(name) and self.cancel(name):
                removed.append(name)
        return tuple(removed)

    def _capability(
        self,
        available: bool,
        failure_code: str | None = None,
        detail: str | None = None,
        *,
        observed: str | None = None,
    ) -> IsolationCapability:
        return IsolationCapability(
            backend=IsolationBackendKind.DOCKER,
            profile_id=self.profile.profile_id,
            available=available,
            image_reference=self.profile.image_reference,
            configured_image_digest=self.profile.image_digest,
            observed_image_digest=observed,
            supported_toolchains=(
                self.profile.supported_toolchains if available else ()
            ),
            supported_actions=_SUPPORTED_ACTIONS if available else (),
            failure_code=failure_code,
            detail=detail,
        )


def create_workspace_snapshot(
    repository_root: str | Path,
    destination: str | Path,
    *,
    max_files: int = 20_000,
    max_bytes: int = 512 * 1024 * 1024,
) -> Path:
    source = Path(repository_root).expanduser().resolve(strict=True)
    target = Path(destination).expanduser().resolve()
    if not source.is_dir():
        raise ProjectExecutionPolicyError(
            "The approved repository root is not a directory."
        )
    if target.exists():
        raise ProjectExecutionPolicyError(
            "The workspace snapshot destination already exists."
        )
    files = 0
    total_bytes = 0
    for directory, names, filenames in os.walk(source, followlinks=False):
        directory_path = Path(directory)
        retained: list[str] = []
        for name in names:
            candidate = directory_path / name
            relative = candidate.relative_to(source)
            if _excluded(relative):
                continue
            if candidate.is_symlink():
                raise ProjectExecutionPolicyError(
                    f"Workspace snapshots reject symlinks: {relative.as_posix()}"
                )
            retained.append(name)
        names[:] = retained
        for name in filenames:
            candidate = directory_path / name
            relative = candidate.relative_to(source)
            if _excluded(relative):
                continue
            if candidate.is_symlink():
                raise ProjectExecutionPolicyError(
                    f"Workspace snapshots reject symlinks: {relative.as_posix()}"
                )
            if not candidate.is_file():
                raise ProjectExecutionPolicyError(
                    f"Workspace snapshots reject unsafe files: {relative.as_posix()}"
                )
            files += 1
            total_bytes += candidate.stat().st_size
            if files > max_files or total_bytes > max_bytes:
                raise ProjectExecutionPolicyError(
                    "The repository exceeds the bounded isolation snapshot limits."
                )
    shutil.copytree(
        source,
        target,
        ignore=_snapshot_ignore,
        copy_function=shutil.copy2,
    )
    _make_snapshot_writable(target)
    return target.resolve(strict=True)


def default_isolation_profile() -> IsolationProfile:
    return IsolationProfile(
        profile_id="astra-python-node-v1",
        image_reference=os.getenv(
            "ASTRA_PROJECT_RUNTIME_IMAGE",
            "astra-project-runtime:stage2c-v1",
        ),
        image_digest=os.getenv(
            "ASTRA_PROJECT_RUNTIME_IMAGE_DIGEST",
            "sha256:" + ("0" * 64),
        ).lower(),
        supported_toolchains=(
            IsolationToolchain.PYTHON,
            IsolationToolchain.NODE,
        ),
    )


def _container_argv(prepared: PreparedExecution) -> list[str]:
    spec = prepared.spec
    arguments = list(spec.arguments)
    if spec.action == WorkerCommandAction.PYTEST:
        return ["python3", "-m", "pytest", *arguments]
    if spec.action == WorkerCommandAction.PYTHON_SCRIPT:
        if not spec.target:
            raise ProjectExecutionPolicyError(
                "Python execution requires an approved target."
            )
        return ["python3", f"/workspace/{spec.target}", *arguments]
    if spec.action == WorkerCommandAction.NPM_TEST:
        return ["npm", "test", "--", *arguments]
    fixed_npm = {
        WorkerCommandAction.NPM_RUN_LINT: "lint",
        WorkerCommandAction.NPM_RUN_BUILD: "build",
        WorkerCommandAction.NPM_RUN_TYPECHECK: "typecheck",
    }
    if spec.action in fixed_npm:
        return ["npm", "run", fixed_npm[spec.action]]
    if spec.action == WorkerCommandAction.NODE_TEST:
        targets = ([spec.target] if spec.target else []) + arguments
        return [
            "node",
            "--test",
            "--experimental-strip-types",
            *[f"/workspace/{item}" for item in targets],
        ]
    raise ProjectExecutionPolicyError(
        "The requested action is not supported by the isolated runtime."
    )


def _docker_client_environment() -> dict[str, str]:
    allowed = {
        "PATH",
        "SYSTEMROOT",
        "WINDIR",
        "DOCKER_HOST",
        "DOCKER_CONTEXT",
    }
    return {
        key: value
        for key, value in os.environ.items()
        if key in allowed
    }


def container_identity_for(worker_request_id: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_.-]", "-", worker_request_id)[:80]
    digest = hashlib.sha256(
        worker_request_id.encode("utf-8", errors="replace")
    ).hexdigest()[:12]
    return f"astra-{safe}-{digest}"


def _excluded(relative: Path) -> bool:
    return any(
        part in _EXCLUDED_NAMES
        or part.startswith(".env")
        or Path(part).suffix.lower() in _EXCLUDED_SUFFIXES
        for part in relative.parts
    )


def _snapshot_ignore(_directory: str, names: list[str]) -> set[str]:
    return {
        name
        for name in names
        if name in _EXCLUDED_NAMES
        or name.startswith(".env")
        or Path(name).suffix.lower() in _EXCLUDED_SUFFIXES
    }


def _make_snapshot_writable(target: Path) -> None:
    """Grant the fixed container UID access only to the disposable snapshot."""
    target.chmod(0o777)
    for directory, names, filenames in os.walk(target, followlinks=False):
        directory_path = Path(directory)
        directory_path.chmod(0o777)
        for name in names:
            child = directory_path / name
            if child.is_symlink():
                raise ProjectExecutionPolicyError(
                    "Workspace snapshots reject symlinks after copying."
                )
            child.chmod(0o777)
        for name in filenames:
            child = directory_path / name
            if child.is_symlink() or not child.is_file():
                raise ProjectExecutionPolicyError(
                    "Workspace snapshots reject unsafe files after copying."
                )
            executable_bits = child.stat().st_mode & 0o111
            child.chmod(0o666 | executable_bits)


def _format_float(value: float) -> str:
    return f"{value:.3f}".rstrip("0").rstrip(".")


def _bounded(value: str, limit: int) -> str:
    return " ".join(value.split())[:limit]


class _BoundedBytes:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.data = bytearray()
        self.truncated = False
        self.lock = threading.Lock()

    def append(self, chunk: bytes) -> None:
        with self.lock:
            remaining = self.limit - len(self.data)
            if remaining > 0:
                self.data.extend(chunk[:remaining])
            if len(chunk) > remaining:
                self.truncated = True

    def text(self) -> str:
        with self.lock:
            return bytes(self.data).decode("utf-8", errors="replace")


def _drain(stream: BinaryIO | None, buffer: _BoundedBytes) -> None:
    if stream is None:
        return
    try:
        while chunk := stream.read(4096):
            buffer.append(chunk)
    finally:
        stream.close()


__all__ = [
    "DockerIsolationBackend",
    "IsolationBackend",
    "create_workspace_snapshot",
    "default_isolation_profile",
]
