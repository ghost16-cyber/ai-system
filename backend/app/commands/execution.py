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


SCHEMA_VERSION = 2
DEFAULT_TIMEOUT_SECONDS = 30
MAX_TIMEOUT_SECONDS = 120
APPROVAL_TTL_SECONDS = 600
MAX_LOG_CHARS = 64_000
ALLOWED_ACTIONS = frozenset(
    {"pytest", "python_script", "streamlit", "docker_ps", "docker_compose_up"}
)
COMPOSE_FILES = ("compose.yml", "compose.yaml", "docker-compose.yml", "docker-compose.yaml")
PYTHON_ENTRY_SCRIPTS = (
    "main.py",
    "app.py",
    "run.py",
    "producer.py",
    "consumer_to_influx.py",
    "pyspark_snowflake.py",
    "structured_streaming_job.py",
)
STREAMLIT_ENTRY_SCRIPTS = ("dashboard/app.py", "streamlit_app.py")
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
    assignment_id: str,
    assignment_task: str,
    expected_result: str,
    action: str,
    target: str | None = None,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    root = Path(project_root).expanduser().resolve()
    workdir = Path(workspace).expanduser().resolve()
    _require_inside(workdir, root, "Command workspace")
    assignment_key = _validate_assignment_id(assignment_id)
    if not assignment_task.strip():
        raise CommandExecutionError("Originating assignment task is required.")
    if not expected_result.strip():
        raise CommandExecutionError("Expected result is required.")
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
        "assignment_id": assignment_key,
        "assignment_task": assignment_task.strip(),
        "expected_result": expected_result.strip(),
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
    return public_command_record(record, project_root=root)


def approve_assignment_command(
    store_root: str | Path,
    plan_id: str,
    *,
    assignment_id: str,
    workspace: str | Path,
    project_root: str | Path,
    confirmation: str,
) -> tuple[dict[str, Any], str]:
    with _STORE_LOCK:
        record = _read_record(store_root, plan_id)
        _verify_assignment_association(record, assignment_id, workspace, project_root)
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
    return public_command_record(record, project_root=project_root), token


def cancel_assignment_command(
    store_root: str | Path,
    plan_id: str,
    *,
    assignment_id: str,
    workspace: str | Path,
    project_root: str | Path,
) -> dict[str, Any]:
    """Persist cancellation of an unapproved plan without executing it."""
    with _STORE_LOCK:
        record = _read_record(store_root, plan_id)
        _verify_assignment_association(record, assignment_id, workspace, project_root)
        if record["status"] != "planned":
            raise CommandExecutionError("Only a pending command plan can be cancelled.")
        _verify_fingerprint(record)
        record["status"] = "cancelled"
        record["finished_at"] = _now()
        record["approval_token_hash"] = None
        _write_record(store_root, record)
    return public_command_record(record, project_root=project_root)


def execute_assignment_command(
    store_root: str | Path,
    project_root: str | Path,
    plan_id: str,
    *,
    assignment_id: str,
    workspace: str | Path,
    approval_token: str,
) -> dict[str, Any]:
    with _STORE_LOCK:
        record = _read_record(store_root, plan_id)
        _verify_assignment_association(record, assignment_id, workspace, project_root)
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
    record["evidence"] = _build_execution_evidence(record)
    with _STORE_LOCK:
        _write_record(store_root, record)
    return public_command_record(record, project_root=project_root)


def get_assignment_command(
    store_root: str | Path,
    plan_id: str,
    *,
    project_root: str | Path | None = None,
    assignment_id: str | None = None,
    workspace: str | Path | None = None,
) -> dict[str, Any]:
    with _STORE_LOCK:
        record = _read_record(store_root, plan_id)
        if assignment_id is not None or workspace is not None:
            if assignment_id is None or workspace is None or project_root is None:
                raise CommandExecutionError("Assignment and workspace association are both required.")
            _verify_assignment_association(record, assignment_id, workspace, project_root)
        _refresh_expired_approval(store_root, record)
        return public_command_record(record, project_root=project_root)


def suggest_assignment_actions(
    project_root: str | Path,
    workspace: str | Path,
) -> list[dict[str, Any]]:
    """Return deterministic, non-executing actions detected from workspace files."""
    root = Path(project_root).expanduser().resolve()
    workdir = Path(workspace).expanduser().resolve()
    _require_inside(workdir, root, "Command workspace")
    if not workdir.is_dir():
        raise CommandExecutionError("Command workspace must already exist and be a directory.")

    detected: list[tuple[str, str | None, str, str]] = []
    has_tests = any(_eligible_regular_file(workdir, name) for name in ("pytest.ini", "tox.ini")) or any(
        path.is_file() and path.name.startswith("test_") and not path.is_symlink()
        for directory in (workdir / "tests", workdir / "test")
        if directory.is_dir() and not directory.is_symlink()
        for path in directory.glob("*.py")
    )
    if has_tests:
        detected.append(("pytest", None, "Validate the assignment's Python test suite.", "Pytest pass/fail summary."))

    for target in PYTHON_ENTRY_SCRIPTS:
        if _eligible_regular_file(workdir, target) and not _looks_like_streamlit(workdir / target):
            detected.append(("python_script", target, f"Validate assignment entry script {target}.", "Script output or a Python traceback."))
    for target in (*STREAMLIT_ENTRY_SCRIPTS, "app.py"):
        if _eligible_regular_file(workdir, target) and _looks_like_streamlit(workdir / target):
            detected.append(("streamlit", target, f"Validate detected Streamlit application {target}.", "Streamlit startup output or an import error."))

    compose = next((name for name in COMPOSE_FILES if _eligible_regular_file(workdir, name)), None)
    has_docker_context = compose is not None or _eligible_regular_file(workdir, "Dockerfile")
    if has_docker_context:
        detected.append(("docker_ps", None, "Inspect Docker status for this assignment workspace.", "Read-only running-container table."))
    if compose is not None:
        detected.append(("docker_compose_up", None, f"Start services declared by {compose} after approval.", "Compose service startup logs."))

    suggestions: list[dict[str, Any]] = []
    for action, target, purpose, expected_result in detected:
        suggestion = suggest_command(action, root, target=target, working_directory=workdir)
        argv = shlex.split(suggestion.command, posix=os.name != "nt")
        suggestions.append({
            "action": action,
            "target": target,
            "executable": argv[0],
            "arguments": argv[1:],
            "command": shlex.join(argv),
            "working_directory": ".",
            "purpose": purpose,
            "expected_result": expected_result,
            "risk_level": "medium" if action == "pytest" else suggestion.risk_level,
            "timeout_seconds": DEFAULT_TIMEOUT_SECONDS,
            "requires_approval": True,
            "executed": False,
        })
    return suggestions


def get_assignment_execution_summary(
    store_root: str | Path,
    project_root: str | Path,
    assignment_id: str,
    workspace: str | Path,
) -> dict[str, Any]:
    root = Path(project_root).expanduser().resolve()
    workdir = Path(workspace).expanduser().resolve()
    _require_inside(workdir, root, "Command workspace")
    assignment_key = _validate_assignment_id(assignment_id)
    records: list[dict[str, Any]] = []
    store = Path(store_root).expanduser().resolve()
    with _STORE_LOCK:
        if store.is_dir():
            for path in sorted(store.glob("*.json")):
                if not re.fullmatch(r"[a-f0-9]{32}\.json", path.name):
                    continue
                record = _read_record(store, path.stem)
                _refresh_expired_approval(store, record)
                if record["assignment_id"] == assignment_key and record["workspace_path"] == str(workdir):
                    records.append(public_command_record(record, project_root=root))
    evidence = [record["evidence"] for record in records if isinstance(record.get("evidence"), dict)]
    return {
        "assignment_id": assignment_key,
        "workspace": workdir.relative_to(root).as_posix(),
        "planned_commands": records,
        "approval_state": _aggregate_state(records, approved=True),
        "execution_state": _aggregate_state(records, approved=False),
        "evidence": evidence,
        "assignment_completion_inferred": False,
        "warnings": [
            "Controlled execution is not an operating-system sandbox.",
            "A successful exit records validation evidence but does not mark academic work complete.",
        ],
        "limitations": [
            "Only deterministic allowlisted actions can be planned.",
            "Network and filesystem isolation are not provided by this executor.",
        ],
    }


def public_command_record(
    record: dict[str, Any], *, project_root: str | Path | None = None
) -> dict[str, Any]:
    public = {
        key: value
        for key, value in record.items()
        if key not in {"approval_token_hash", "workspace_path"}
    }
    workspace = Path(record["workspace_path"])
    if project_root is not None:
        try:
            public["workspace"] = workspace.relative_to(Path(project_root).resolve()).as_posix()
        except ValueError:
            public["workspace"] = workspace.name
    else:
        public["workspace"] = workspace.name
    public["executable"] = record["argv"][0]
    public["arguments"] = list(record["argv"][1:])
    public["working_directory"] = "."
    public["display_state"] = _display_state(record["status"])
    public["log_available"] = bool(record.get("stdout") or record.get("stderr"))
    public["redacted_output_summary"] = {
        "stdout": str(record.get("stdout") or "")[:1000],
        "stderr": str(record.get("stderr") or "")[:1000],
        "truncated": bool(record.get("log_truncated")),
    }
    return public


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
            for name in COMPOSE_FILES
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
        for name in COMPOSE_FILES:
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
        "assignment_id": str,
        "assignment_task": str,
        "expected_result": str,
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
            "assignment_id",
            "assignment_task",
            "expected_result",
            "purpose",
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


def _validate_assignment_id(value: str) -> str:
    assignment_id = value.strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", assignment_id):
        raise CommandExecutionError("Assignment identifier is invalid.")
    return assignment_id


def _verify_assignment_association(
    record: dict[str, Any],
    assignment_id: str,
    workspace: str | Path,
    project_root: str | Path,
) -> None:
    root = Path(project_root).expanduser().resolve()
    workdir = Path(workspace).expanduser().resolve()
    _require_inside(workdir, root, "Command workspace")
    if record["assignment_id"] != _validate_assignment_id(assignment_id):
        raise CommandExecutionError("Command plan belongs to a different assignment.")
    if not secrets.compare_digest(record["workspace_path"], str(workdir)):
        raise CommandExecutionError("Command plan belongs to a different workspace.")


def _refresh_expired_approval(store_root: str | Path, record: dict[str, Any]) -> None:
    if record.get("status") != "approved":
        return
    try:
        expires = datetime.fromisoformat(record["approval_expires_at"])
    except (TypeError, ValueError):
        return
    if datetime.now(timezone.utc) >= expires:
        record["status"] = "approval_expired"
        record["approval_token_hash"] = None
        _write_record(store_root, record)


def _display_state(status: str) -> str:
    return {
        "planned": "pending",
        "approved": "approved",
        "running": "running",
        "succeeded": "completed",
        "failed": "failed",
        "timed_out": "failed",
        "approval_expired": "expired",
        "cancelled": "cancelled",
    }.get(status, "failed")


def _build_execution_evidence(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "evidence_id": f"execution-{record['plan_id']}",
        "assignment_id": record["assignment_id"],
        "assignment_task": record["assignment_task"],
        "command_type": record["action"],
        "exit_code": record.get("exit_code"),
        "started_at": record.get("started_at"),
        "finished_at": record.get("finished_at"),
        "audit_record_reference": f"assignment-command:{record['plan_id']}",
        "process_succeeded": record.get("exit_code") == 0,
        "academic_completion_inferred": False,
    }


def _eligible_regular_file(workspace: Path, relative: str) -> bool:
    candidate = workspace / relative
    current = workspace
    for part in Path(relative).parts:
        current = current / part
        if current.is_symlink():
            return False
    if not candidate.is_file():
        return False
    try:
        candidate.resolve().relative_to(workspace)
    except ValueError:
        return False
    return True


def _looks_like_streamlit(path: Path) -> bool:
    try:
        sample = path.read_text(encoding="utf-8", errors="replace")[:64_000]
    except OSError:
        return False
    return bool(re.search(r"(?m)^\s*(?:import\s+streamlit|from\s+streamlit\s+import)\b", sample))


def _aggregate_state(records: list[dict[str, Any]], *, approved: bool) -> str:
    if not records:
        return "none"
    states = [record["display_state"] for record in records]
    if approved:
        if "approved" in states:
            return "approved"
        if "expired" in states:
            return "expired"
        return "pending" if "pending" in states else "consumed"
    for state in ("running", "failed", "completed", "cancelled", "expired", "approved", "pending"):
        if state in states:
            return state
    return "pending"


def _redact(value: str) -> str:
    redacted = _SECRET_ASSIGNMENT_RE.sub(r"\1\2<redacted>", value)
    return _OPENAI_KEY_RE.sub("<redacted-api-key>", redacted)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
