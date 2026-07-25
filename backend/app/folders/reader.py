from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
import re

from backend.app.folders.safety import (
    ProjectSafetyError,
    project_file_exclusion_reason,
    resolve_project_path,
    safe_relative_path,
)
from backend.app.folders.scanner import is_ignored_directory_name, validate_folder_root


@dataclass(frozen=True)
class ReadLimits:
    max_file_size: int = 512_000
    max_bytes_per_file: int = 128_000
    max_total_bytes: int = 512_000
    max_files: int = 80
    max_excerpts: int = 20
    max_lines_per_excerpt: int = 40
    max_context_chars: int = 120_000


def read_project_file(
    root: str | Path,
    relative_path: str,
    *,
    limits: ReadLimits | None = None,
) -> dict:
    limits = limits or ReadLimits()
    approved = validate_folder_root(str(root))
    relative = safe_relative_path(relative_path)
    path = resolve_project_path(approved, relative)
    try:
        size = path.stat().st_size
    except PermissionError:
        return _skipped(relative, "permission_denied", 0)
    reason = project_file_exclusion_reason(path, approved, size=size)
    if reason:
        return _skipped(relative, reason, size)
    if size > limits.max_file_size:
        return _skipped(relative, "file_size_limit", size)
    try:
        with path.open("rb") as handle:
            raw = handle.read(limits.max_bytes_per_file + 1)
    except PermissionError:
        return _skipped(relative, "permission_denied", size)
    except OSError:
        return _skipped(relative, "unreadable_file", size)
    truncated = len(raw) > limits.max_bytes_per_file
    raw = raw[: limits.max_bytes_per_file]
    if b"\x00" in raw or _looks_binary(raw):
        return _skipped(relative, "binary_file", size)
    try:
        text = raw.decode("utf-8-sig")
        encoding = "utf-8"
    except UnicodeDecodeError:
        try:
            text = raw.decode("latin-1")
            encoding = "latin-1"
        except UnicodeDecodeError:
            return _skipped(relative, "unsupported_encoding", size)
    if _contains_sensitive_content(text):
        return _skipped(relative, "sensitive_content", size)
    return {
        "relative_path": relative,
        "status": "readable",
        "reason": None,
        "text": text,
        "size_bytes": size,
        "bytes_read": len(raw),
        "encoding": encoding,
        "truncated": truncated,
        "line_count": len(text.splitlines()),
    }


def iter_project_files(root: str | Path, *, max_files: int = 500) -> Iterable[Path]:
    approved = validate_folder_root(str(root))
    yielded = 0
    for current, dirnames, filenames in __import__("os").walk(approved, topdown=True, followlinks=False):
        current_path = Path(current)
        dirnames[:] = [
            name for name in sorted(dirnames)
            if not is_ignored_directory_name(name)
            and name != "vendor"
            and not (current_path / name).is_symlink()
        ]
        for name in sorted(filenames):
            if yielded >= max_files:
                return
            path = current_path / name
            if path.is_symlink():
                continue
            yielded += 1
            yield path


def line_excerpt(text: str, start_line: int, end_line: int) -> str:
    lines = text.splitlines()
    start = max(1, start_line)
    end = min(len(lines), max(start, end_line))
    return "\n".join(f"{index}: {lines[index - 1]}" for index in range(start, end + 1))


def _skipped(relative: str, reason: str, size: int) -> dict:
    return {
        "relative_path": relative,
        "status": "skipped",
        "reason": reason,
        "text": "",
        "size_bytes": size,
        "bytes_read": 0,
        "encoding": None,
        "truncated": False,
        "line_count": 0,
    }


def _looks_binary(raw: bytes) -> bool:
    if not raw:
        return False
    control = sum(1 for value in raw if value < 9 or 13 < value < 32)
    return control / len(raw) > 0.20


def _contains_sensitive_content(text: str) -> bool:
    if re.search(r"\bsk-[A-Za-z0-9_-]{12,}\b", text):
        return True
    return bool(re.search(
        r"(?im)^\s*(?:export\s+)?[A-Za-z0-9_.-]*(?:password|passwd|secret|api[_-]?key|access[_-]?token|private[_-]?key)[A-Za-z0-9_.-]*\s*[:=]\s*['\"]?[^\s'\"]{4,}",
        text,
    ))


__all__ = ["ProjectSafetyError", "ReadLimits", "iter_project_files", "line_excerpt", "read_project_file"]
