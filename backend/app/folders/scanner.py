from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.app.core.path_utils import normalize_path_for_platform


class FolderScanConfigError(ValueError):
    """Raised when a scan-limit environment variable holds an invalid value.

    Configuration errors fail the process at import time rather than
    silently falling back to an unbounded or nonsensical scan -- there is
    intentionally no implicit unlimited mode.
    """


def read_positive_int_env(env_name: str, default: int) -> int:
    raw = os.environ.get(env_name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError as error:
        raise FolderScanConfigError(f"{env_name}={raw!r} is not a valid integer.") from error
    if value <= 0:
        raise FolderScanConfigError(
            f"{env_name}={raw!r} must be a positive integer; unlimited scanning is not supported."
        )
    return value


# Astra's own repository (backend + frontend + tests + docs, with generated
# run-history/caches excluded before budget accounting and its RAG corpus /
# benchmark fixtures exempted as recognized dataset content) consumes the
# file-count budget at ~750-800 eligible files -- 1500 leaves substantial
# headroom without approaching an unbounded scan. See
# docs/astra-phase10-1-scanner-limits.md.
MAX_FILES = read_positive_int_env("ASTRA_SCAN_MAX_FILES", 1500)
MAX_FILE_SIZE_BYTES = read_positive_int_env("ASTRA_SCAN_MAX_FILE_SIZE_BYTES", 5 * 1024 * 1024)
MAX_TOTAL_SIZE_BYTES = read_positive_int_env("ASTRA_SCAN_MAX_TOTAL_SIZE_BYTES", 150 * 1024 * 1024)
MAX_DEPTH = read_positive_int_env("ASTRA_SCAN_MAX_DEPTH", 12)

# Hard safety valve on top of the configured file-count budget: scanning
# still walks the whole tree (bounded by MAX_DEPTH) to report precise
# omitted-file diagnostics, but never inspects more raw filesystem entries
# than this, regardless of configuration.
MAX_SCAN_DIAGNOSTIC_ENTRIES = 50_000

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
    "dist",
    "build",
    "coverage",
    ".next",
    ".cache",
    # Phase 10.1: additional generic generated/local-artefact directories.
    ".qa",
    ".work",
    "logs",
    "htmlcov",
    ".tox",
    ".nox",
    ".eggs",
    ".turbo",
    ".parcel-cache",
    ".docusaurus",
    "checkpoints",
    # Named (not pattern-matched) generated run-history directories: these
    # are excluded by exact name wherever they appear in the tree, the same
    # way node_modules/dist/build already are -- not via a blanket dot-rule.
    ".runs",
}

# Phase 10.1.1: hidden directories that hold meaningful repository
# configuration, not generated/local state. These are never excluded,
# regardless of what future generic dot-directory heuristics might suggest --
# excluding them would silently drop CI workflows or dev-container/editor
# configuration from the manifest while still reporting it "complete".
ALLOWED_HIDDEN_DIRS = {".github", ".devcontainer", ".vscode"}


def is_ignored_directory_name(name: str) -> bool:
    """Directory-level exclusion, applied before any per-file budget accounting.

    This is a deterministic classification, not a blanket "hidden directories
    are generated" heuristic: only directories named in IGNORED_DIRS are
    excluded. An unrecognized hidden directory (e.g. an editor's or a
    project-specific tool's dotfolder that isn't on either list) is *not*
    silently treated as generated -- it is scanned like any other directory,
    so a manifest can never be reported complete while quietly omitting
    content nobody has actually classified.
    """
    if name in ALLOWED_HIDDEN_DIRS:
        return False
    return name in IGNORED_DIRS


# Reference/dataset content that Astra's manifest-completeness policy
# already treats as non-authoritative for project execution (see
# project_analysis.state_manifest's required-entry check, which reuses this
# same predicate). Exempt content is still scanned and may still appear in
# the manifest when small enough -- it just never competes with source,
# config, or test files for the file-count scan budget.
DATASET_EXEMPT_TOP_LEVEL_DIRS = {"assignment_inputs", "datasets", "evidence", "astra_corpus", "benchmarks"}
DATASET_EXEMPT_SUFFIXES = {".csv", ".tsv", ".parquet", ".jsonl", ".docx", ".pdf", ".png", ".jpg", ".jpeg", ".webp"}


def is_budget_exempt_dataset_content(relative_path: str, suffix: str) -> bool:
    first = relative_path.split("/", 1)[0].lower() if relative_path else ""
    return first in DATASET_EXEMPT_TOP_LEVEL_DIRS or suffix.lower() in DATASET_EXEMPT_SUFFIXES


TEMPORARY_SUFFIXES = {".tmp", ".temp", ".log", ".bak", ".swp", ".swo", ".orig", ".rej"}


def _is_temporary_file(name: str, suffix: str) -> bool:
    return suffix in TEMPORARY_SUFFIXES or name.endswith("~")


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


@dataclass(frozen=True, slots=True)
class FolderScanLimits:
    max_files: int = MAX_FILES
    max_file_size_bytes: int = MAX_FILE_SIZE_BYTES
    max_total_size_bytes: int = MAX_TOTAL_SIZE_BYTES
    max_depth: int = MAX_DEPTH


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


_IGNORE_REASON_BUCKETS = {
    "ignored_directory": "ignored_generated",
    "symlink_directory": "ignored_generated",
    "symlink_file": "ignored_generated",
    "symlink_escape": "ignored_generated",
    "sensitive_file": "ignored_sensitive",
    "windows_download_metadata": "ignored_sensitive",
    "blocked_file_type": "ignored_unsupported",
    "temporary_file": "ignored_temporary",
    "file_size_limit": "oversized",
    "unreadable_or_outside_root": "unreadable",
}


def build_inventory(root: Path, *, limits: FolderScanLimits | None = None) -> dict[str, Any]:
    limits = limits or FolderScanLimits(
        max_files=MAX_FILES, max_file_size_bytes=MAX_FILE_SIZE_BYTES,
        max_total_size_bytes=MAX_TOTAL_SIZE_BYTES, max_depth=MAX_DEPTH,
    )
    approved_root = root.resolve()
    items: list[dict[str, Any]] = []
    warnings: list[str] = []
    total_size = 0
    eligible_seen = 0
    eligible_omitted = 0
    total_size_budget_exceeded = False
    max_depth_reached = False
    diagnostic_cap_reached = False
    diagnostic_cap = max(MAX_SCAN_DIAGNOSTIC_ENTRIES, limits.max_files * 20)
    scanned_entries = 0
    counts = {bucket: 0 for bucket in set(_IGNORE_REASON_BUCKETS.values())}
    exempt_dataset_files = 0

    for current, dirnames, filenames in os.walk(approved_root, topdown=True, followlinks=False):
        current_path = Path(current)
        depth = len(current_path.relative_to(approved_root).parts)
        if depth >= limits.max_depth:
            dirnames[:] = []
            max_depth_reached = True
            warnings.append(f"Maximum scan depth reached at {current_path.relative_to(approved_root).as_posix() or '.'}.")

        kept_dirs = []
        for dirname in sorted(dirnames):
            directory = current_path / dirname
            if is_ignored_directory_name(dirname):
                items.append(_ignored_item(directory, approved_root, "ignored_directory"))
                counts["ignored_generated"] += 1
                continue
            if directory.is_symlink():
                try:
                    target = directory.resolve()
                    target.relative_to(approved_root)
                except (OSError, ValueError):
                    items.append(_ignored_item(directory, approved_root, "symlink_escape"))
                    counts["ignored_generated"] += 1
                    continue
                items.append(_ignored_item(directory, approved_root, "symlink_directory"))
                counts["ignored_generated"] += 1
                continue
            kept_dirs.append(dirname)
        dirnames[:] = kept_dirs

        for filename in sorted(filenames):
            if scanned_entries >= diagnostic_cap:
                diagnostic_cap_reached = True
                break
            scanned_entries += 1
            path = current_path / filename
            item = _inventory_item(path, approved_root, max_file_size_bytes=limits.max_file_size_bytes)
            if item["status"] != "readable":
                bucket = _IGNORE_REASON_BUCKETS.get(str(item.get("ignore_reason") or ""))
                if bucket:
                    counts[bucket] += 1
                items.append(item)
                continue
            if is_budget_exempt_dataset_content(str(item["relative_path"]), str(item["extension"])):
                exempt_dataset_files += 1
                items.append(item)
                continue
            eligible_seen += 1
            if eligible_seen > limits.max_files:
                eligible_omitted += 1
                items.append({
                    **item,
                    "status": "ignored",
                    "classification": "ignored",
                    "ignore_reason": "file_count_budget_exceeded",
                })
                continue
            total_size += int(item["size_bytes"])
            if total_size > limits.max_total_size_bytes:
                total_size_budget_exceeded = True
                item = {
                    **item,
                    "status": "ignored",
                    "classification": "ignored",
                    "ignore_reason": "total_size_limit",
                }
            items.append(item)
        if diagnostic_cap_reached:
            warnings.append(f"Scan diagnostics truncated after {diagnostic_cap} filesystem entries.")
            break

    if eligible_omitted:
        warnings.append(
            f"Maximum file count reached; scan was truncated. "
            f"{eligible_omitted} eligible file(s) beyond the {limits.max_files}-file limit were omitted."
        )
    if total_size_budget_exceeded:
        warnings.append("Maximum total scan size reached; remaining readable files were ignored.")

    diagnostics = {
        "total_indexed": sum(1 for item in items if item.get("status") == "readable"),
        "total_eligible": eligible_seen,
        "eligible_omitted": eligible_omitted,
        "exempt_dataset_files": exempt_dataset_files,
        "ignored_generated": counts["ignored_generated"],
        "ignored_sensitive": counts["ignored_sensitive"],
        "ignored_unsupported": counts["ignored_unsupported"],
        "ignored_temporary": counts["ignored_temporary"],
        "oversized": counts["oversized"],
        "unreadable": counts["unreadable"],
        "file_count_budget_exceeded": eligible_omitted > 0,
        "total_size_budget_exceeded": total_size_budget_exceeded,
        "max_depth_reached": max_depth_reached,
        "diagnostic_cap_reached": diagnostic_cap_reached,
    }
    complete = not (
        diagnostics["file_count_budget_exceeded"]
        or diagnostics["total_size_budget_exceeded"]
        or diagnostics["max_depth_reached"]
    )
    summary = _summary(items, warnings)
    return {
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "root_display_name": safe_display_path(approved_root),
        "summary": summary,
        "inventory": items,
        "warnings": warnings,
        "complete": complete,
        "diagnostics": diagnostics,
        "limits": {
            "max_files": limits.max_files,
            "max_file_size_bytes": limits.max_file_size_bytes,
            "max_total_size_bytes": limits.max_total_size_bytes,
            "max_depth": limits.max_depth,
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


def _inventory_item(path: Path, root: Path, *, max_file_size_bytes: int = MAX_FILE_SIZE_BYTES) -> dict[str, Any]:
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
    ignore_reason = _ignore_reason(resolved, stat.st_size, max_file_size_bytes=max_file_size_bytes)
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


def _ignore_reason(path: Path, size: int, *, max_file_size_bytes: int = MAX_FILE_SIZE_BYTES) -> str | None:
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
    if _is_temporary_file(name, suffix):
        return "temporary_file"
    if size > max_file_size_bytes:
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
