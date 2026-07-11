from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shlex
import signal
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.app.commands.suggestions import suggest_command


SCHEMA_VERSION = 1
DEFAULT_TIMEOUT_SECONDS = 30
MAX_TIMEOUT_SECONDS = 120
APPROVAL_TTL_SECONDS = 600
MAX_LOG_CHARS = 64_000
ALLOWED_ACTIONS = frozenset(
    {"pytest", "python_script", "streamlit", "docker_ps", "docker_compose_up"}
)
_STORE_LOCK = threading.Lock()
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(password|passwd|token|secret|api[_-]?key|access[_-]?key)\b(\s*[:=]\s*)([^\s,;]+)"
)
_OPENAI_KEY_RE = re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b")


class CommandExecutionError(ValueError):
    pass


def plan_assignment_command(
    store_root: str | Path,
    project_root: str | Path,
    workspace: str | Path,
    *,
    action: str,
    target: str | None = None,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    root = Path(project_root).expanduser().resolve()
    workdir = Path(workspace).expanduser().resolve()
    _require_inside(workdir, root, "Command workspace")
    if not workdir.is_dir():
        raise CommandExecutionError("Command workspace must already exist and be a directory.")
    action_key = action.strip().lower().replace("-", "_")
    if action_key not in ALLOWED_ACTIONS:
        raise CommandExecutionError("Command action is not allowlisted.")
    if not 1 <= timeout_seconds <= MAX_TIMEOUT_SECONDS:
        raise CommandExecutionError(
            f"timeout_seconds must be between 1 and {MAX_TIMEOUT_SECONDS}."
        )

    suggestion = suggest_command(
        action_key,
        root,
        target=target,
        working_directory=workdir,
    )
    if not suggestion.allowed:
        raise CommandExecutionError("Command suggestion was rejected by policy.")
    argv = shlex.split(suggestion.command, posix=os.name != "nt")
    _validate_argv(action_key, argv, workdir)
    approved_artifacts = _approved_artifacts(action_key, argv, workdir)

    created_at = _now()
    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "plan_id": uuid4().hex,
        "action": action_key,
        "target": target,
        "argv": argv,
        "command": shlex.join(argv),
        "workspace_path": str(workdir),
        "purpose": suggestion.purpose,
        "risk_level": "medium" if action_key == "pytest" else suggestion.risk_level,
        "why_safe": suggestion.why_safe,
        "expected_output_hint": suggestion.expected_output_hint,
        "timeout_seconds": timeout_seconds,
        "status": "planned",
        "approval_required": True,
        "approval_expires_at": None,
        "approval_token_hash": None,
        "created_at": created_at,
        "approved_at": None,
        "started_at": None,
        "finished_at": None,
        "exit_code": None,
        "stdout": "",
        "stderr": "",
        "log_truncated": False,
        "error": None,
        "shell_used": False,
        "environment_policy": "minimal_no_secrets",
        "isolation_level": "controlled_subprocess_not_os_sandboxed",
        "safety_limitations": [
            "Allowlisting and approval do not make an approved Python script trustworthy.",
            "This executor does not provide an operating-system filesystem or network sandbox.",
        ],
        "approved_artifacts": approved_artifacts,
    }
    record["fingerprint"] = _fingerprint(record)
    with _STORE_LOCK:
        _write_record(store_root, record)
    return public_command_record(record)


def approve_assignment_command(
    store_root: str | Path,
    plan_id: str,
    *,
    confirmation: str,
) -> tuple[dict[str, Any], str]:
    with _STORE_LOCK:
        record = _read_record(store_root, plan_id)
        if record["status"] != "planned":
            raise CommandExecutionError("Only a planned command can be approved.")
        expected = f"APPROVE {plan_id}"
        if confirmation != expected:
            raise CommandExecutionError(
                f"Explicit confirmation must exactly match: {expected}"
            )
        _verify_fingerprint(record)
        token = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        record["status"] = "approved"
        record["approved_at"] = now.isoformat()
        record["approval_expires_at"] = (
            now + timedelta(seconds=APPROVAL_TTL_SECONDS)
        ).isoformat()
        record["approval_token_hash"] = _token_hash(token)
        _write_record(store_root, record)
    return public_command_record(record), token


def execute_assignment_command(
    store_root: str | Path,
    project_root: str | Path,
    plan_id: str,
    *,
    approval_token: str,
) -> dict[str, Any]:
    with _STORE_LOCK:
        record = _read_record(store_root, plan_id)
        if record["status"] != "approved":
            raise CommandExecutionError("Command must be approved and can execute only once.")
        _verify_fingerprint(record)
        try:
            expires = datetime.fromisoformat(record["approval_expires_at"])
        except (TypeError, ValueError) as error:
            raise CommandExecutionError("Command approval metadata is malformed.") from error
        if datetime.now(timezone.utc) >= expires:
            record["status"] = "approval_expired"
            record["approval_token_hash"] = None
            _write_record(store_root, record)
            raise CommandExecutionError("Command approval has expired; create a new plan.")
        stored_token_hash = record.get("approval_token_hash")
        if not isinstance(stored_token_hash, str) or not secrets.compare_digest(
            stored_token_hash, _token_hash(approval_token)
        ):
            raise CommandExecutionError("Invalid approval token.")

        workdir = Path(record["workspace_path"]).resolve()
        root = Path(project_root).expanduser().resolve()
        _require_inside(workdir, root, "Command workspace")
        _validate_argv(record["action"], list(record["argv"]), workdir)
        _verify_approved_artifacts(record, workdir)
        record["status"] = "running"
        record["started_at"] = _now()
        record["approval_token_hash"] = None
        _write_record(store_root, record)

    try:
        result = _run_bounded(
            list(record["argv"]),
            workdir,
            int(record["timeout_seconds"]),
        )
        record.update(result)
        record["status"] = (
            "timed_out"
            if result["timed_out"]
            else "succeeded" if result["exit_code"] == 0 else "failed"
        )
    except Exception as error:  # keep execution failures controlled and auditable
        record["status"] = "failed"
        record["error"] = _redact(str(error))
    record["finished_at"] = _now()
    with _STORE_LOCK:
        _write_record(store_root, record)
    return public_command_record(record)


def get_assignment_command(store_root: str | Path, plan_id: str) -> dict[str, Any]:
    with _STORE_LOCK:
        return public_command_record(_read_record(store_root, plan_id))


def public_command_record(record: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in record.items()
        if key not in {"approval_token_hash"}
    }


def _validate_argv(action: str, argv: list[str], workspace: Path) -> None:
    expected_fixed = {
        "pytest": ["python", "-m", "pytest", "-q"],
        "docker_ps": ["docker", "ps"],
        "docker_compose_up": ["docker", "compose", "up"],
    }
    if action in expected_fixed:
        if argv != expected_fixed[action]:
            raise CommandExecutionError("Command arguments do not match the allowlisted action.")
        if action == "docker_compose_up" and not any(
            (workspace / name).is_file()
            for name in ("compose.yml", "compose.yaml", "docker-compose.yml", "docker-compose.yaml")
        ):
            raise CommandExecutionError("Docker Compose file not found in the command workspace.")
        return
    if action == "python_script" and len(argv) == 2 and argv[0] == "python":
        _validate_target(argv[1], workspace, {".py"})
        return
    if action == "streamlit" and len(argv) == 3 and argv[:2] == ["streamlit", "run"]:
        _validate_target(argv[2], workspace, {".py"})
        return
    raise CommandExecutionError("Command shape does not match its allowlisted action.")


def _validate_target(raw_target: str, workspace: Path, extensions: set[str]) -> None:
    target = Path(raw_target)
    if target.is_absolute() or ".." in target.parts or target.suffix.lower() not in extensions:
        raise CommandExecutionError("Command target must be an allowlisted relative file.")
    candidate = workspace / target
    current = workspace
    for part in target.parts:
        current = current / part
        if current.is_symlink():
            raise CommandExecutionError("Command targets cannot traverse symlinks.")
    resolved = candidate.resolve()
    _require_inside(resolved, workspace, "Command target")
    if not resolved.is_file():
        raise CommandExecutionError("Command target file does not exist.")


def _approved_artifacts(action: str, argv: list[str], workspace: Path) -> list[dict[str, str]]:
    paths: list[Path] = []
    if action == "python_script":
        paths.append(workspace / argv[1])
    elif action == "streamlit":
        paths.append(workspace / argv[2])
    elif action == "docker_compose_up":
        for name in ("compose.yml", "compose.yaml", "docker-compose.yml", "docker-compose.yaml"):
            candidate = workspace / name
            if candidate.is_file():
                paths.append(candidate)
                break
    return [
        {
            "path": path.relative_to(workspace).as_posix(),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for path in paths
    ]


def _verify_approved_artifacts(record: dict[str, Any], workspace: Path) -> None:
    artifacts = record.get("approved_artifacts")
    if not isinstance(artifacts, list):
        raise CommandExecutionError("Command plan artifact metadata is malformed.")
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            raise CommandExecutionError("Command plan artifact metadata is malformed.")
        relative = artifact.get("path")
        expected_hash = artifact.get("sha256")
        if not isinstance(relative, str) or not isinstance(expected_hash, str):
            raise CommandExecutionError("Command plan artifact metadata is malformed.")
        candidate = workspace / relative
        _validate_target(relative, workspace, {candidate.suffix.lower()})
        actual_hash = hashlib.sha256(candidate.read_bytes()).hexdigest()
        if not secrets.compare_digest(actual_hash, expected_hash):
            raise CommandExecutionError(
                f"Approved command artifact changed after planning: {relative}"
            )


def _run_bounded(argv: list[str], workspace: Path, timeout_seconds: int) -> dict[str, Any]:
    runtime_argv = list(argv)
    if runtime_argv[0] == "python":
        runtime_argv[0] = sys.executable
    elif runtime_argv[:2] == ["streamlit", "run"]:
        runtime_argv = [sys.executable, "-m", "streamlit", *runtime_argv[1:]]
    environment = {
        key: value
        for key, value in os.environ.items()
        if key in {"PATH", "LANG", "LC_ALL", "SYSTEMROOT", "WINDIR", "PATHEXT", "VIRTUAL_ENV"}
    }
    environment.update({"PYTHONUNBUFFERED": "1", "ASTRA_COMMAND_EXECUTION": "1"})
    process = subprocess.Popen(
        runtime_argv,
        cwd=workspace,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
        shell=False,
        start_new_session=os.name != "nt",
    )
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    counters = {"stdout": 0, "stderr": 0}

    def drain(stream, parts: list[str], key: str) -> None:
        assert stream is not None
        for chunk in iter(lambda: stream.read(4096), ""):
            remaining = MAX_LOG_CHARS - counters[key]
            if remaining > 0:
                parts.append(chunk[:remaining])
                counters[key] += min(len(chunk), remaining)

    threads = [
        threading.Thread(target=drain, args=(process.stdout, stdout_parts, "stdout"), daemon=True),
        threading.Thread(target=drain, args=(process.stderr, stderr_parts, "stderr"), daemon=True),
    ]
    for thread in threads:
        thread.start()
    timed_out = False
    try:
        process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        _terminate_process(process)
    for thread in threads:
        thread.join(timeout=2)
    stdout = "".join(stdout_parts)
    stderr = "".join(stderr_parts)
    return {
        "exit_code": process.returncode,
        "stdout": _redact(stdout),
        "stderr": _redact(stderr),
        "log_truncated": counters["stdout"] >= MAX_LOG_CHARS or counters["stderr"] >= MAX_LOG_CHARS,
        "timed_out": timed_out,
        "error": "Command exceeded its approved timeout." if timed_out else None,
    }


def _terminate_process(process: subprocess.Popen[str]) -> None:
    try:
        if os.name != "nt":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
        process.wait(timeout=2)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            if os.name != "nt":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
            process.wait(timeout=2)
        except ProcessLookupError:
            pass


def _record_path(store_root: str | Path, plan_id: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{32}", plan_id):
        raise CommandExecutionError("Invalid command plan id.")
    return Path(store_root).expanduser().resolve() / f"{plan_id}.json"


def _read_record(store_root: str | Path, plan_id: str) -> dict[str, Any]:
    path = _record_path(store_root, plan_id)
    if not path.is_file():
        raise FileNotFoundError(f"Command plan not found: {plan_id}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CommandExecutionError("Command plan record is malformed.") from error
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        raise CommandExecutionError("Command plan record has an incompatible schema.")
    required_types = {
        "plan_id": str,
        "status": str,
        "action": str,
        "argv": list,
        "workspace_path": str,
        "timeout_seconds": int,
        "fingerprint": str,
        "approved_artifacts": list,
    }
    if any(not isinstance(value.get(key), expected) for key, expected in required_types.items()):
        raise CommandExecutionError("Command plan record is malformed.")
    if value["plan_id"] != plan_id or value["action"] not in ALLOWED_ACTIONS:
        raise CommandExecutionError("Command plan record is malformed.")
    if not all(isinstance(item, str) for item in value["argv"]):
        raise CommandExecutionError("Command plan record is malformed.")
    return value


def _write_record(store_root: str | Path, record: dict[str, Any]) -> None:
    root = Path(store_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    path = _record_path(root, record["plan_id"])
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _fingerprint(record: dict[str, Any]) -> str:
    payload = {
        key: record[key]
        for key in (
            "action",
            "target",
            "argv",
            "workspace_path",
            "timeout_seconds",
            "approved_artifacts",
        )
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _verify_fingerprint(record: dict[str, Any]) -> None:
    if not secrets.compare_digest(record.get("fingerprint", ""), _fingerprint(record)):
        raise CommandExecutionError("Command plan integrity check failed.")


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _require_inside(path: Path, root: Path, label: str) -> None:
    try:
        path.relative_to(root)
    except ValueError as error:
        raise CommandExecutionError(f"{label} must stay inside the configured workspace root.") from error


def _redact(value: str) -> str:
    redacted = _SECRET_ASSIGNMENT_RE.sub(r"\1\2<redacted>", value)
    return _OPENAI_KEY_RE.sub("<redacted-api-key>", redacted)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
