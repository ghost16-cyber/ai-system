from __future__ import annotations

import hashlib
import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from backend.app.folders.safety import ProjectSafetyError, safe_relative_path, validate_root_identity
from backend.app.project_control.contracts import ExecutionAttemptType
from backend.app.project_workers.contracts import ProjectWorkerRequest
from backend.app.project_workers.runtime_contracts import (
    WorkerCommandAction,
    WorkerExecutionSpec,
    calculate_execution_hash,
)


class ProjectExecutionPolicyError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PreparedExecution:
    spec: WorkerExecutionSpec
    repository_root: Path
    working_directory: Path
    argv: tuple[str, ...]


_SUPPORTED_ATTEMPTS = frozenset({ExecutionAttemptType.COMMAND, ExecutionAttemptType.VERIFICATION})
_BLOCKED_PYTEST_OPTIONS = (
    "-p",
    "--pyargs",
    "--rootdir",
    "--confcutdir",
    "--basetemp",
    "--override-ini",
)
_SAFE_ARGUMENT_RE = re.compile(r"^[^\x00\r\n]{0,500}$")


def prepare_execution(request: ProjectWorkerRequest) -> PreparedExecution:
    if request.attempt_type not in _SUPPORTED_ATTEMPTS:
        raise ProjectExecutionPolicyError(
            "The bounded subprocess executor only supports command and verification attempts."
        )
    try:
        spec = WorkerExecutionSpec.model_validate(request.payload.get("execution"))
    except Exception as error:
        raise ProjectExecutionPolicyError(
            "The worker request does not contain a valid execution specification."
        ) from error

    calculated = calculate_execution_hash(spec)
    if spec.execution_hash.lower() != calculated:
        raise ProjectExecutionPolicyError("The execution specification integrity hash is invalid.")
    authority_hash = str(request.authority.get("execution_hash") or "").lower()
    if authority_hash != spec.execution_hash.lower():
        raise ProjectExecutionPolicyError(
            "The canonical execution attempt does not authorize this exact execution specification."
        )

    if request.attempt_type == ExecutionAttemptType.COMMAND:
        command_id = str(request.authority.get("command_id") or "")
        if not spec.command_id or spec.command_id != command_id:
            raise ProjectExecutionPolicyError(
                "The execution specification is not bound to the approved command."
            )
    else:
        criterion_id = str(request.authority.get("criterion_id") or "")
        if not spec.criterion_id or spec.criterion_id != criterion_id or not spec.criterion_hash:
            raise ProjectExecutionPolicyError(
                "The execution specification is not bound to the requested verification criterion."
            )

    try:
        root = validate_root_identity(request.repository_root, request.repository_root_fingerprint)
    except (ProjectSafetyError, FileNotFoundError, OSError) as error:
        raise ProjectExecutionPolicyError(
            "The approved repository root is missing or its identity changed."
        ) from error

    workdir = _resolve_directory(root, spec.working_directory)
    artifact_hashes = _validate_artifacts(root, spec)
    argv = _build_argv(root, workdir, spec, artifact_hashes)
    return PreparedExecution(
        spec=spec,
        repository_root=root,
        working_directory=workdir,
        argv=tuple(argv),
    )


def minimal_environment(runtime_root: Path) -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if key in {"PATH", "LANG", "LC_ALL", "SYSTEMROOT", "WINDIR", "PATHEXT", "VIRTUAL_ENV"}
    }
    environment.update({
        "HOME": str(runtime_root),
        "USERPROFILE": str(runtime_root),
        "TMPDIR": str(runtime_root),
        "TEMP": str(runtime_root),
        "TMP": str(runtime_root),
        "PYTHONUNBUFFERED": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPYCACHEPREFIX": str(runtime_root / "pycache"),
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "NPM_CONFIG_CACHE": str(runtime_root / "npm-cache"),
        "ASTRA_BOUNDED_EXECUTION": "1",
    })
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        environment.pop(key, None)
    return environment


def _resolve_directory(root: Path, relative_value: str) -> Path:
    relative = relative_value.strip().replace("\\", "/")
    if relative in {"", "."}:
        return root
    try:
        safe = safe_relative_path(relative)
    except ProjectSafetyError as error:
        raise ProjectExecutionPolicyError(
            "The execution working directory must remain inside the approved repository."
        ) from error
    candidate = root
    for part in safe.split("/"):
        candidate = candidate / part
        if candidate.is_symlink():
            raise ProjectExecutionPolicyError("Symlink working directories are not allowed.")
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (FileNotFoundError, OSError, ValueError) as error:
        raise ProjectExecutionPolicyError(
            "The execution working directory is missing or escapes the repository."
        ) from error
    if not resolved.is_dir():
        raise ProjectExecutionPolicyError("The execution working directory must be a directory.")
    return resolved


def _resolve_file(root: Path, relative_value: str) -> tuple[str, Path]:
    try:
        relative = safe_relative_path(relative_value)
    except ProjectSafetyError as error:
        raise ProjectExecutionPolicyError(
            "Execution input paths must remain inside the approved repository."
        ) from error
    candidate = root
    for part in relative.split("/"):
        candidate = candidate / part
        if candidate.is_symlink():
            raise ProjectExecutionPolicyError("Symlink execution inputs are not allowed.")
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (FileNotFoundError, OSError, ValueError) as error:
        raise ProjectExecutionPolicyError(
            f"Approved execution input is missing or unsafe: {relative}"
        ) from error
    if not resolved.is_file():
        raise ProjectExecutionPolicyError(
            f"Approved execution input is not a regular file: {relative}"
        )
    return relative, resolved


def _validate_artifacts(root: Path, spec: WorkerExecutionSpec) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for artifact in spec.input_artifacts:
        relative, path = _resolve_file(root, artifact.relative_path)
        expected = artifact.sha256.lower()
        if not re.fullmatch(r"[a-f0-9]{64}", expected):
            raise ProjectExecutionPolicyError(f"Approved artifact hash is invalid: {relative}")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            raise ProjectExecutionPolicyError(
                f"Approved execution input changed after approval: {relative}"
            )
        hashes[relative] = expected
    return hashes


def _build_argv(
    root: Path,
    workdir: Path,
    spec: WorkerExecutionSpec,
    artifact_hashes: dict[str, str],
) -> list[str]:
    arguments = [_validate_argument(item) for item in spec.arguments]
    action = spec.action

    if action == WorkerCommandAction.PYTEST:
        _validate_pytest_arguments(root, arguments)
        return [sys.executable, "-m", "pytest", *arguments]

    if action == WorkerCommandAction.PYTHON_SCRIPT:
        target = _required_target(spec)
        relative, path = _resolve_file(root, target)
        if path.suffix.lower() != ".py" or relative not in artifact_hashes:
            raise ProjectExecutionPolicyError(
                "Python execution requires an approved, hash-bound .py target."
            )
        return [sys.executable, str(path), *arguments]

    if action in {
        WorkerCommandAction.NPM_TEST,
        WorkerCommandAction.NPM_RUN_LINT,
        WorkerCommandAction.NPM_RUN_BUILD,
        WorkerCommandAction.NPM_RUN_TYPECHECK,
    }:
        package_relative = workdir.relative_to(root).joinpath("package.json").as_posix()
        _relative, package_file = _resolve_file(root, package_relative)
        if package_relative not in artifact_hashes:
            raise ProjectExecutionPolicyError(
                "NPM execution requires an approved, hash-bound package.json."
            )
        if package_file.name != "package.json":
            raise ProjectExecutionPolicyError("The approved NPM manifest is invalid.")
        npm = shutil.which("npm")
        if not npm:
            raise ProjectExecutionPolicyError("npm is not available in the worker environment.")
        if action == WorkerCommandAction.NPM_TEST:
            return [npm, "test", "--", *arguments]
        if arguments:
            raise ProjectExecutionPolicyError(
                "Fixed npm run actions do not accept extra arguments."
            )
        script = {
            WorkerCommandAction.NPM_RUN_LINT: "lint",
            WorkerCommandAction.NPM_RUN_BUILD: "build",
            WorkerCommandAction.NPM_RUN_TYPECHECK: "typecheck",
        }[action]
        return [npm, "run", script]

    if action == WorkerCommandAction.NODE_TEST:
        node = shutil.which("node")
        if not node:
            raise ProjectExecutionPolicyError("node is not available in the worker environment.")
        targets = ([spec.target] if spec.target else []) + arguments
        if not targets:
            raise ProjectExecutionPolicyError(
                "Node test execution requires at least one approved test file."
            )
        resolved_targets: list[str] = []
        for target in targets:
            relative, path = _resolve_file(root, target)
            if path.suffix.lower() not in {".js", ".mjs", ".cjs", ".ts", ".mts", ".cts"}:
                raise ProjectExecutionPolicyError(
                    "Node test targets must be JavaScript or TypeScript files."
                )
            if relative not in artifact_hashes:
                raise ProjectExecutionPolicyError(f"Node test target is not hash-bound: {relative}")
            resolved_targets.append(str(path))
        return [node, "--test", "--experimental-strip-types", *resolved_targets]

    if action == WorkerCommandAction.DOCKER_PS:
        if spec.target or arguments:
            raise ProjectExecutionPolicyError(
                "docker ps does not accept project-supplied arguments."
            )
        docker = shutil.which("docker")
        if not docker:
            raise ProjectExecutionPolicyError("docker is not available in the worker environment.")
        return [docker, "ps", "--format", "{{.ID}}\t{{.Image}}\t{{.Status}}\t{{.Names}}"]

    raise ProjectExecutionPolicyError("The requested command action is not allowlisted.")


def _required_target(spec: WorkerExecutionSpec) -> str:
    if not spec.target:
        raise ProjectExecutionPolicyError(f"{spec.action.value} requires an approved target.")
    return spec.target


def _validate_argument(value: str) -> str:
    if not isinstance(value, str) or not _SAFE_ARGUMENT_RE.fullmatch(value):
        raise ProjectExecutionPolicyError(
            "Execution arguments contain invalid characters or exceed limits."
        )
    return value


def _validate_pytest_arguments(root: Path, arguments: list[str]) -> None:
    consume_expression = False
    for index, argument in enumerate(arguments):
        if consume_expression:
            consume_expression = False
            continue
        if argument in {"-k", "-m"}:
            if index + 1 >= len(arguments):
                raise ProjectExecutionPolicyError(f"{argument} requires a bounded expression.")
            consume_expression = True
            continue
        if argument in _BLOCKED_PYTEST_OPTIONS or any(
            argument.startswith(f"{option}=") for option in _BLOCKED_PYTEST_OPTIONS
        ):
            raise ProjectExecutionPolicyError(
                f"Pytest option is blocked by execution policy: {argument}"
            )
        if argument.startswith("-"):
            allowed = (
                argument in {"-q", "--quiet", "-x", "--exitfirst", "--disable-warnings"}
                or argument.startswith("--maxfail=")
                or argument.startswith("--tb=")
            )
            if not allowed:
                raise ProjectExecutionPolicyError(
                    f"Pytest option is not allowlisted: {argument}"
                )
            continue
        selector_path = argument.split("::", 1)[0]
        _resolve_file(root, selector_path)
