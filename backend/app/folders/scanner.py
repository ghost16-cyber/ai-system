from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.app.core.path_utils import normalize_path_for_platform


MAX_FILES = 500
MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024
MAX_TOTAL_SIZE_BYTES = 50 * 1024 * 1024
MAX_DEPTH = 12

IGNORED_DIRS = {
    ".git",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".idea",
    ".vscode",
    "dist",
    "build",
    "coverage",
    ".next",
    ".cache",
}

SENSITIVE_NAMES = {
    ".env",
    "credentials",
    "credentials.json",
    "token",
    "token.json",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
}

BLOCKED_SUFFIXES = {
    ".db",
    ".sqlite",
    ".sqlite3",
    ".duckdb",
    ".pkl",
    ".pickle",
    ".pt",
    ".pth",
    ".onnx",
    ".bin",
    ".exe",
    ".dll",
    ".so",
    ".dylib",
    ".pyc",
    ".class",
    ".zip",
    ".tar",
    ".gz",
    ".7z",
    ".rar",
}

ASSIGNMENT_SUFFIXES = {".pdf", ".docx", ".txt", ".md"}
DATASET_SUFFIXES = {".csv", ".tsv", ".parquet", ".json", ".jsonl"}
SOURCE_SUFFIXES = {
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".java",
    ".cpp",
    ".c",
    ".h",
    ".sql",
    ".ipynb",
}
REPORT_SUFFIXES = {".md", ".txt", ".docx", ".pdf"}
EVIDENCE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
CONFIG_SUFFIXES = {".toml", ".yaml", ".yml", ".ini", ".cfg", ".json"}


class FolderScanError(ValueError):
    pass


def validate_folder_root(raw_path: str) -> Path:
    if raw_path is None or not str(raw_path).strip():
        raise FolderScanError("Folder path is required.")
    normalized = normalize_path_for_platform(raw_path)
    if any(part == ".." for part in re.split(r"[\\/]+", normalized.raw_path)):
        raise FolderScanError("Folder path cannot contain '..'.")
    candidate = normalized.path
    resolved = candidate.resolve() if candidate.is_absolute() else candidate.expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"Folder not found: {safe_display_path(resolved)}")
    if not resolved.is_dir():
        raise FolderScanError(f"Folder path must point to a directory: {safe_display_path(resolved)}")
    return resolved


def safe_display_path(path: str | Path) -> str:
    value = Path(path)
    name = value.name or str(value)
    parent = value.parent.name
    return f"{parent}/{name}" if parent else name


def build_inventory(root: Path) -> dict[str, Any]:
    approved_root = root.resolve()
    items: list[dict[str, Any]] = []
    warnings: list[str] = []
    total_size = 0
    limit_reached = False

    for current, dirnames, filenames in os.walk(approved_root, topdown=True, followlinks=False):
        current_path = Path(current)
        depth = len(current_path.relative_to(approved_root).parts)
        if depth >= MAX_DEPTH:
            dirnames[:] = []
            warnings.append(f"Maximum scan depth reached at {current_path.relative_to(approved_root).as_posix() or '.'}.")

        kept_dirs = []
        for dirname in sorted(dirnames):
            directory = current_path / dirname
            if dirname in IGNORED_DIRS:
                items.append(_ignored_item(directory, approved_root, "ignored_directory"))
                continue
            if directory.is_symlink():
                try:
                    target = directory.resolve()
                    target.relative_to(approved_root)
                except (OSError, ValueError):
                    items.append(_ignored_item(directory, approved_root, "symlink_escape"))
                    continue
                items.append(_ignored_item(directory, approved_root, "symlink_directory"))
                continue
            kept_dirs.append(dirname)
        dirnames[:] = kept_dirs

        for filename in sorted(filenames):
            path = current_path / filename
            if len(items) >= MAX_FILES:
                limit_reached = True
                break
            item = _inventory_item(path, approved_root)
            if item["status"] == "readable":
                total_size += int(item["size_bytes"])
                if total_size > MAX_TOTAL_SIZE_BYTES:
                    item["status"] = "ignored"
                    item["classification"] = "ignored"
                    item["ignore_reason"] = "total_size_limit"
                    warnings.append("Maximum total scan size reached; remaining readable files were ignored.")
            items.append(item)
        if limit_reached:
            warnings.append("Maximum file count reached; scan was truncated.")
            break

    summary = _summary(items, warnings)
    return {
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "root_display_name": safe_display_path(approved_root),
        "summary": summary,
        "inventory": items,
        "warnings": warnings,
        "limits": {
            "max_files": MAX_FILES,
            "max_file_size_bytes": MAX_FILE_SIZE_BYTES,
            "max_total_size_bytes": MAX_TOTAL_SIZE_BYTES,
            "max_depth": MAX_DEPTH,
        },
    }


def diff_inventories(previous: list[dict[str, Any]], current: list[dict[str, Any]]) -> dict[str, int]:
    old = {str(item.get("relative_path")): item for item in previous if item.get("relative_path")}
    new = {str(item.get("relative_path")): item for item in current if item.get("relative_path")}
    added = set(new) - set(old)
    deleted = set(old) - set(new)
    common = set(old) & set(new)
    changed = {
        path
        for path in common
        if old[path].get("fingerprint") != new[path].get("fingerprint")
    }
    unchanged = common - changed
    return {
        "added": len(added),
        "changed": len(changed),
        "deleted": len(deleted),
        "unchanged": len(unchanged),
    }


def _inventory_item(path: Path, root: Path) -> dict[str, Any]:
    try:
        if path.is_symlink():
            target = path.resolve()
            try:
                target.relative_to(root)
            except ValueError:
                return _ignored_item(path, root, "symlink_escape")
            return _ignored_item(path, root, "symlink_file")
        resolved = path.resolve()
        resolved.relative_to(root)
        stat = resolved.stat()
    except (OSError, ValueError):
        return _ignored_item(path, root, "unreadable_or_outside_root")

    relative = resolved.relative_to(root).as_posix()
    suffix = resolved.suffix.lower()
    ignore_reason = _ignore_reason(resolved, stat.st_size)
    if ignore_reason:
        return {
            **_metadata(resolved, root, stat.st_size),
            "classification": "ignored",
            "status": "ignored",
            "ignore_reason": ignore_reason,
        }
    return {
        **_metadata(resolved, root, stat.st_size),
        "relative_path": relative,
        "filename": resolved.name,
        "extension": suffix,
        "classification": classify_file(relative, suffix),
        "status": "readable",
        "ignore_reason": None,
    }


def _metadata(path: Path, root: Path, size: int) -> dict[str, Any]:
    stat = path.stat()
    relative = path.relative_to(root).as_posix()
    fingerprint = hashlib.sha256(
        f"{relative}:{size}:{stat.st_mtime_ns}".encode("utf-8")
    ).hexdigest()
    return {
        "relative_path": relative,
        "filename": path.name,
        "extension": path.suffix.lower(),
        "size_bytes": size,
        "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
        "fingerprint": fingerprint,
    }


def _ignored_item(path: Path, root: Path, reason: str) -> dict[str, Any]:
    try:
        relative = path.relative_to(root).as_posix()
    except ValueError:
        relative = path.name
    size = 0
    modified_at = None
    try:
        stat = path.lstat()
        size = stat.st_size
        modified_at = datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()
    except OSError:
        pass
    return {
        "relative_path": relative,
        "filename": path.name,
        "classification": "ignored",
        "extension": path.suffix.lower(),
        "size_bytes": size,
        "modified_at": modified_at,
        "fingerprint": hashlib.sha256(f"{relative}:{reason}:{size}".encode("utf-8")).hexdigest(),
        "status": "ignored",
        "ignore_reason": reason,
    }


def _ignore_reason(path: Path, size: int) -> str | None:
    name = path.name.lower()
    suffix = path.suffix.lower()
    if name.endswith(":zone.identifier"):
        return "windows_download_metadata"
    if name in SENSITIVE_NAMES or name.startswith(".env"):
        return "sensitive_file"
    if "credential" in name or "secret" in name or "token" in name:
        return "sensitive_file"
    if suffix in BLOCKED_SUFFIXES:
        return "blocked_file_type"
    if size > MAX_FILE_SIZE_BYTES:
        return "file_size_limit"
    return None


def file_ignore_reason(path: Path, size: int) -> str | None:
    """Public scanner policy hook shared by safe project readers and patching."""
    return _ignore_reason(path, size)


def classify_file(relative_path: str, suffix: str) -> str:
    lower = relative_path.lower()
    name = Path(lower).name
    if suffix in DATASET_SUFFIXES:
        return "dataset"
    if suffix in SOURCE_SUFFIXES:
        return "source_code"
    if suffix in EVIDENCE_SUFFIXES:
        return "evidence"
    if suffix in CONFIG_SUFFIXES or name in {"dockerfile", "makefile"}:
        return "configuration"
    if suffix in ASSIGNMENT_SUFFIXES and "assignment" in lower:
        return "assignment"
    if suffix in REPORT_SUFFIXES and ("report" in lower or "readme" in name):
        return "report"
    if suffix in REPORT_SUFFIXES:
        return "documentation"
    return "other"


def _summary(items: list[dict[str, Any]], warnings: list[str]) -> dict[str, int]:
    readable = [item for item in items if item.get("status") == "readable"]
    ignored = [item for item in items if item.get("status") == "ignored"]
    return {
        "total_discovered": len(items),
        "readable": len(readable),
        "ignored": len(ignored),
        "assignments": _count(readable, "assignment"),
        "datasets": _count(readable, "dataset"),
        "source_files": _count(readable, "source_code"),
        "reports": _count(readable, "report"),
        "evidence_files": _count(readable, "evidence"),
        "configuration_files": _count(readable, "configuration"),
        "other_files": _count(readable, "other") + _count(readable, "documentation"),
        "warning_count": len(warnings),
    }


def _count(items: list[dict[str, Any]], classification: str) -> int:
    return sum(1 for item in items if item.get("classification") == classification)
