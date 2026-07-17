from __future__ import annotations

import hashlib
import json
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.app.folders.safety import project_root_fingerprint, safe_relative_path
from backend.app.folders.scanner import FolderScanLimits, build_inventory, validate_folder_root


MANIFEST_VERSION = "astra.project-state-manifest.v1"
SCANNER_POLICY_VERSION = "astra.folder-scanner-policy.v2"


class ManifestStrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProjectStateEntry(ManifestStrictModel):
    normalized_relative_path: str
    file_type: str
    size: int = Field(ge=0)
    content_hash: str = Field(min_length=64, max_length=64)
    mode: int = Field(ge=0)


class ProjectStateManifest(ManifestStrictModel):
    schema_version: Literal["astra.project-state-manifest.v1"] = MANIFEST_VERSION
    workspace_id: str
    root_fingerprint: str = Field(min_length=64, max_length=64)
    generated_at: datetime
    scanner_policy_version: str
    entries: tuple[ProjectStateEntry, ...]
    excluded_summary: dict[str, int]
    limits: dict[str, int]
    complete: bool
    incomplete_reasons: tuple[str, ...] = ()
    manifest_hash: str = Field(min_length=64, max_length=64)


@dataclass(frozen=True, slots=True)
class ProjectManifestLimits:
    max_files: int = 5_000
    max_file_size_bytes: int = 10 * 1024 * 1024
    max_total_size_bytes: int = 200 * 1024 * 1024
    max_depth: int = 24
    stream_chunk_bytes: int = 1024 * 1024


DEFAULT_MANIFEST_LIMITS = ProjectManifestLimits()


class ProjectManifestError(ValueError):
    code = "project_manifest_error"


class IncompleteProjectManifestError(ProjectManifestError):
    code = "incomplete_project_manifest"

    def __init__(self, message: str, *, manifest: ProjectStateManifest | None = None) -> None:
        super().__init__(message)
        self.manifest = manifest


def build_project_state_manifest(
    root: str | Path, *, workspace_id: str | None = None,
    limits: ProjectManifestLimits = DEFAULT_MANIFEST_LIMITS,
    require_complete: bool = True,
) -> ProjectStateManifest:
    approved = validate_folder_root(str(root))
    scan = build_inventory(approved, limits=FolderScanLimits(
        max_files=limits.max_files, max_file_size_bytes=limits.max_file_size_bytes,
        max_total_size_bytes=limits.max_total_size_bytes, max_depth=limits.max_depth,
    ))
    entries: list[ProjectStateEntry] = []
    excluded: dict[str, int] = {}
    incomplete_reasons: list[str] = []
    for item in scan.get("inventory", []):
        if item.get("status") != "readable":
            reason = str(item.get("ignore_reason") or "ignored")
            excluded[reason] = excluded.get(reason, 0) + 1
            if reason in {"total_size_limit", "unreadable_or_outside_root"} or (
                reason == "file_size_limit" and _is_required_manifest_entry(item)
            ):
                incomplete_reasons.append(reason)
            continue
        relative = safe_relative_path(str(item["relative_path"]))
        path = approved.joinpath(*relative.split("/"))
        try:
            resolved = path.resolve(strict=True)
            resolved.relative_to(approved)
            if path.is_symlink() or not resolved.is_file():
                incomplete_reasons.append(f"unsafe_file:{relative}")
                continue
            digest = _stream_hash(resolved, limits.stream_chunk_bytes)
            mode = stat.S_IMODE(resolved.stat().st_mode)
        except (OSError, ValueError):
            incomplete_reasons.append(f"unreadable:{relative}")
            continue
        entries.append(ProjectStateEntry(
            normalized_relative_path=relative,
            file_type=str(item.get("classification") or "other"),
            size=int(item.get("size_bytes") or 0), content_hash=digest, mode=mode,
        ))
    for warning in scan.get("warnings", []):
        if "Maximum" in str(warning):
            incomplete_reasons.append(str(warning))
    entries.sort(key=lambda entry: entry.normalized_relative_path)
    complete = bool(scan.get("complete", True)) and not incomplete_reasons
    content = {
        "schema_version": MANIFEST_VERSION,
        "workspace_id": workspace_id or project_root_fingerprint(approved),
        "root_fingerprint": project_root_fingerprint(approved),
        "scanner_policy_version": SCANNER_POLICY_VERSION,
        "entries": [entry.model_dump(mode="json") for entry in entries],
        "excluded_summary": dict(sorted(excluded.items())),
        "limits": {
            "max_files": limits.max_files,
            "max_file_size_bytes": limits.max_file_size_bytes,
            "max_total_size_bytes": limits.max_total_size_bytes,
            "max_depth": limits.max_depth,
        },
        "complete": complete,
        "incomplete_reasons": sorted(set(incomplete_reasons)),
    }
    digest = _canonical_hash(content)
    manifest = ProjectStateManifest(
        **content, generated_at=datetime.now(timezone.utc), manifest_hash=digest,
    )
    if require_complete and not manifest.complete:
        reasons = "; ".join(manifest.incomplete_reasons[:5]) or "configured scan limit reached"
        raise IncompleteProjectManifestError(
            f"Project state manifest is incomplete: {reasons}. Increase a safe limit and rescan.",
            manifest=manifest,
        )
    return manifest


def assert_manifest_fresh(
    manifest: ProjectStateManifest | dict, root: str | Path,
) -> ProjectStateManifest:
    prior = manifest if isinstance(manifest, ProjectStateManifest) else ProjectStateManifest.model_validate(manifest)
    if not prior.complete:
        raise IncompleteProjectManifestError("An incomplete project manifest cannot authorize an action.", manifest=prior)
    current = build_project_state_manifest(root, workspace_id=prior.workspace_id)
    if current.manifest_hash != prior.manifest_hash:
        raise ProjectManifestError("The project state changed after the trusted manifest was created.")
    return current


def _stream_hash(path: Path, chunk_size: int) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _is_required_manifest_entry(item: dict) -> bool:
    """Large input artifacts may be excluded; project logic/configuration may not.

    Dataset, assignment, and captured evidence contents are intentionally represented
    by the exclusion policy instead of being loaded into the execution authorization
    manifest. Source, configuration, and general project files remain fail-closed.
    """
    relative = str(item.get("relative_path") or "")
    suffix = str(item.get("extension") or Path(relative).suffix).lower()
    first = relative.split("/", 1)[0].lower()
    if first in {"assignment_inputs", "datasets", "evidence"}:
        return False
    if suffix in {".csv", ".tsv", ".parquet", ".jsonl", ".docx", ".pdf", ".png", ".jpg", ".jpeg", ".webp"}:
        return False
    return True


def _canonical_hash(value: dict) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


__all__ = [
    "DEFAULT_MANIFEST_LIMITS", "IncompleteProjectManifestError", "MANIFEST_VERSION",
    "ProjectManifestError", "ProjectManifestLimits", "ProjectStateEntry",
    "ProjectStateManifest", "assert_manifest_fresh", "build_project_state_manifest",
]
