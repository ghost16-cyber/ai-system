from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.app.folders.reader import iter_project_files
from backend.app.folders.safety import project_root_fingerprint, safe_relative_path
from backend.app.project_analysis.diagnosis.models import (
    FAILURE_EVIDENCE_VERSION, MAX_MODEL_FAILURE_CHARS, MAX_REFERENCED_FILES,
    MAX_STDERR_CHARS, MAX_STDOUT_CHARS, MAX_STORED_DIAGNOSTIC_CHARS,
    ProjectFailureEvidence,
)
from backend.app.project_analysis.diagnosis.parsers import parse_failure_output


_ANSI_RE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(password|passwd|token|secret|api[_-]?key|access[_-]?key)\b(\s*[:=]\s*)([^\s,;]+)"
)
_LIVE_SECRET_RE = re.compile(
    r"(?:sk-(?:proj-)?[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16})"
)
_APPROVAL_RE = re.compile(r"(?i)\bAPPROVE\s+(?:PATCH|ROLLBACK)?\s*[A-Za-z0-9_-]*")
_EXTERNAL_UNIX_RE = re.compile(r"(?<![\w.])/(?:[^\s:'\"]+/)*[^\s:'\"]+")
_EXTERNAL_WINDOWS_RE = re.compile(r"(?i)(?<!\w)[A-Z]:[\\/](?:[^\s:'\"]+[\\/])*[^\s:'\"]+")
_SHELL_INSTRUCTION_RE = re.compile(r"(?i)\b(?:rm\s+-rf|curl\s+|wget\s+|printenv\b|cat\s+\.env\b|sudo\s+)")


def build_failure_evidence(
    root: str | Path,
    *,
    job: dict[str, Any],
    parent_patch: dict[str, Any],
    command: dict[str, Any],
) -> ProjectFailureEvidence:
    approved = Path(root).resolve()
    if command.get("exit_code") == 0 and not command.get("timed_out"):
        raise ValueError("Successful commands do not create failure evidence.")
    if command.get("status") not in {"failed", "timed_out"} and command.get("display_state") != "failed":
        raise ValueError("Only a completed approved command failure can create repair evidence.")
    execution_id = str(command.get("execution_id") or "")
    if not execution_id:
        raise ValueError("Failed command execution identity is required.")
    raw_stdout = str(command.get("stdout") or "")
    raw_stderr = str(command.get("stderr") or "")
    raw = f"{raw_stdout}\n{raw_stderr}"
    bounded_raw = raw[:MAX_STORED_DIAGNOSTIC_CHARS]
    stdout, stdout_reasons = _sanitize_output(raw_stdout, approved, MAX_STDOUT_CHARS)
    stderr, stderr_reasons = _sanitize_output(raw_stderr, approved, MAX_STDERR_CHARS)
    diagnostics, failing_tests, frames = parse_failure_output(f"{stdout}\n{stderr}", root=approved)
    referenced = list(dict.fromkeys(
        [item.relative_path for item in diagnostics if item.relative_path]
        + [item.relative_path for item in frames]
    ))[:MAX_REFERENCED_FILES]
    uncertainty = []
    truncated = bool(command.get("log_truncated")) or len(raw) > MAX_STORED_DIAGNOSTIC_CHARS or len(raw_stdout) > MAX_STDOUT_CHARS or len(raw_stderr) > MAX_STDERR_CHARS
    if truncated:
        uncertainty.append("output_truncated")
    if diagnostics and all(item.tool == "generic" for item in diagnostics):
        uncertainty.append("unsupported_output_format")
    if not referenced:
        uncertainty.append("no_verified_project_path")
    redactions = list(dict.fromkeys([*stdout_reasons, *stderr_reasons]))
    return ProjectFailureEvidence(
        contract_version=FAILURE_EVIDENCE_VERSION,
        evidence_id=uuid4().hex,
        project_job_id=str(job["job_id"]),
        conversation_id=str(job["conversation_id"]),
        folder_access_id=str(job["folder_access_id"]),
        parent_patch_id=str(parent_patch["patch_id"]),
        patch_application_id=str(parent_patch.get("applied_at") or parent_patch["patch_id"]),
        command_plan_id=str(command["plan_id"]),
        command_execution_id=execution_id,
        command_category=str(command.get("action") or "unknown")[:80],
        command_identity=_command_identity(command),
        working_directory=safe_relative_path(str(command.get("workspace") or ".")) if str(command.get("workspace") or ".") != "." else ".",
        root_fingerprint_before=str(job["root_fingerprint"]),
        root_fingerprint_after=project_root_fingerprint(approved),
        project_state_hash=project_state_hash(approved),
        execution_started_at=command.get("started_at"),
        execution_finished_at=command.get("finished_at"),
        exit_code=command.get("exit_code"),
        timed_out=bool(command.get("timed_out")),
        output_truncated=truncated,
        stdout_summary=stdout,
        stderr_summary=stderr,
        diagnostics=diagnostics,
        failing_tests=failing_tests,
        traceback_frames=frames,
        referenced_files=referenced,
        validation_tool=str(command.get("action") or "unknown")[:100],
        output_hash=hashlib.sha256(bounded_raw.encode("utf-8", errors="replace")).hexdigest(),
        redaction_summary=redactions,
        uncertainty_codes=uncertainty,
        created_at=datetime.now(timezone.utc).isoformat(),
    )


def model_failure_text(evidence: ProjectFailureEvidence) -> str:
    sections = [
        "<UNTRUSTED_FAILURE_STDOUT>\n" + evidence.stdout_summary + "\n</UNTRUSTED_FAILURE_STDOUT>",
        "<UNTRUSTED_FAILURE_STDERR>\n" + evidence.stderr_summary + "\n</UNTRUSTED_FAILURE_STDERR>",
    ]
    return "\n".join(sections)[:MAX_MODEL_FAILURE_CHARS]


def project_state_hash(root: str | Path, *, max_files: int = 160) -> str:
    approved = Path(root).resolve()
    values: list[str] = []
    for path in iter_project_files(approved, max_files=max_files):
        try:
            relative = safe_relative_path(path.relative_to(approved).as_posix())
            values.append(f"{relative}:{hashlib.sha256(path.read_bytes()).hexdigest()}")
        except (OSError, ValueError):
            continue
    return hashlib.sha256("\n".join(sorted(values)).encode("utf-8")).hexdigest()


def _sanitize_output(value: str, root: Path, limit: int) -> tuple[str, list[str]]:
    reasons: list[str] = []
    text = _ANSI_RE.sub("", value)
    if text != value:
        reasons.append("terminal_sequences_removed")
    cleaned = []
    for character in text.replace("\x00", ""):
        category = unicodedata.category(character)
        if character in "\n\r\t" or not category.startswith("C"):
            cleaned.append(character)
    controlled = "".join(cleaned)
    if controlled != text:
        reasons.append("control_characters_removed")
    text = controlled
    text, count = _SECRET_ASSIGNMENT_RE.subn(r"\1\2[redacted]", text)
    text, live_count = _LIVE_SECRET_RE.subn("[redacted credential]", text)
    if count or live_count:
        reasons.append("secret_like_values_redacted")
    text, approvals = _APPROVAL_RE.subn("[approval phrase treated as data]", text)
    if approvals:
        reasons.append("approval_phrases_neutralized")
    text, shell_count = _SHELL_INSTRUCTION_RE.subn("[shell instruction treated as data]", text)
    if shell_count:
        reasons.append("shell_instructions_neutralized")
    text = _redact_external_paths(text, root, reasons)
    text, repeated = _suppress_repeated_lines(text)
    if repeated:
        reasons.append("repeated_lines_suppressed")
    if len(text) > limit:
        head = max(0, limit // 2 - 40)
        tail = max(0, limit - head - 80)
        text = text[:head] + "\n[bounded output omitted]\n" + text[-tail:]
        reasons.append("output_truncated")
    return text, reasons


def _redact_external_paths(text: str, root: Path, reasons: list[str]) -> str:
    def replace(match: re.Match[str]) -> str:
        raw = match.group(0)
        try:
            candidate = Path(raw).resolve()
            relative = candidate.relative_to(root)
            return relative.as_posix()
        except (OSError, ValueError):
            reasons.append("external_paths_removed")
            return "[external path omitted]"
    text = _EXTERNAL_WINDOWS_RE.sub(replace, text)
    return _EXTERNAL_UNIX_RE.sub(replace, text)


def _suppress_repeated_lines(text: str) -> tuple[str, bool]:
    counts: dict[str, int] = {}
    output = []
    repeated = False
    for line in text.splitlines():
        key = line.strip()
        counts[key] = counts.get(key, 0) + 1
        if key and counts[key] > 3:
            repeated = True
            continue
        output.append(line)
    return "\n".join(output), repeated


def _command_identity(command: dict[str, Any]) -> str:
    action = str(command.get("action") or "unknown")
    target = str(command.get("target") or "").strip()
    return f"{action}:{target}"[:200]


__all__ = ["build_failure_evidence", "model_failure_text", "project_state_hash"]
