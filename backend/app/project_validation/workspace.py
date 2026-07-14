from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from backend.app.project_validation.contracts import (
    BaselineSnapshot,
    SnapshotFile,
    ValidationLimits,
    WorkspaceReference,
    stable_hash,
)

DEFAULT_EXCLUSIONS = {
    ".git", "node_modules", ".venv", "venv", "__pycache__", ".pytest_cache",
    "dist", "build", "coverage", ".next", ".cache", ".astra_validation_snapshots",
}


class WorkspaceSecurityError(ValueError):
    pass


def _resolved_inside(candidate: Path, root: Path) -> Path:
    resolved = candidate.expanduser().resolve(strict=True)
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise WorkspaceSecurityError("The requested validation path is outside the authorized project folder.") from error
    return resolved


def validate_authorized_root(root: str | Path, *, authorization_id: str, conversation_id: str) -> Path:
    if not authorization_id or not conversation_id:
        raise WorkspaceSecurityError("Validation requires an explicit folder authorization and conversation owner.")
    value = Path(root).expanduser().resolve(strict=True)
    if not value.is_dir():
        raise WorkspaceSecurityError("The authorized validation root must be a directory.")
    return value


def root_fingerprint(root: Path, *, authorization_id: str, conversation_id: str) -> str:
    return stable_hash(f"{root}\0{authorization_id}\0{conversation_id}")


def prepare_workspace(
    source_root: str | Path,
    *,
    authorization_id: str,
    conversation_id: str,
    isolation_parent: str | Path | None = None,
    copy_workspace: bool = False,
) -> WorkspaceReference:
    source = validate_authorized_root(source_root, authorization_id=authorization_id, conversation_id=conversation_id)
    target = source
    isolated = False
    if copy_workspace:
        parent = Path(isolation_parent or source.parent / ".astra_validation").expanduser().resolve()
        parent.mkdir(parents=True, exist_ok=True)
        target = parent / f"workspace-{uuid4().hex[:12]}"
        if target.exists():
            raise WorkspaceSecurityError("The generated validation workspace already exists.")
        shutil.copytree(source, target, symlinks=True, ignore=shutil.ignore_patterns(*DEFAULT_EXCLUSIONS))
        _verify_symlinks(target)
        isolated = True
    return WorkspaceReference(
        workspace_id=f"workspace-{uuid4().hex}",
        authorization_id=authorization_id,
        conversation_id=conversation_id,
        root_fingerprint=root_fingerprint(source, authorization_id=authorization_id, conversation_id=conversation_id),
        display_name=source.name or "Authorized project",
        source_root=str(source),
        validation_root=str(target),
        isolated=isolated,
        prepared_at=datetime.now(timezone.utc),
    )


def _verify_symlinks(root: Path) -> None:
    root = root.resolve()
    for path in root.rglob("*"):
        if not path.is_symlink():
            continue
        try:
            path.resolve(strict=True).relative_to(root)
        except (OSError, ValueError) as error:
            raise WorkspaceSecurityError(f"A symbolic link escapes the validation workspace: {path.name}") from error


def _git_metadata(root: Path) -> tuple[str | None, str | None, bool | None]:
    try:
        branch = subprocess.run(
            ["git", "-C", str(root), "branch", "--show-current"], capture_output=True, text=True, timeout=5, check=False,
        )
        commit = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5, check=False,
        )
        status = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain"], capture_output=True, text=True, timeout=5, check=False,
        )
        if commit.returncode != 0:
            return None, None, None
        return branch.stdout.strip() or None, commit.stdout.strip() or None, bool(status.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        return None, None, None


def capture_snapshot(
    workspace: WorkspaceReference,
    *,
    campaign_id: str,
    limits: ValidationLimits,
    exclusions: set[str] | None = None,
    create_backup: bool = True,
) -> BaselineSnapshot:
    root = Path(workspace.validation_root).resolve(strict=True)
    source = Path(workspace.source_root).resolve(strict=True)
    if workspace.root_fingerprint != root_fingerprint(source, authorization_id=workspace.authorization_id, conversation_id=workspace.conversation_id):
        raise WorkspaceSecurityError("The workspace authorization fingerprint no longer matches.")
    _verify_symlinks(root)
    ignored = set(DEFAULT_EXCLUSIONS if exclusions is None else exclusions)
    files: list[SnapshotFile] = []
    total_bytes = 0
    for current, directories, names in os.walk(root, followlinks=False):
        current_path = Path(current)
        directories[:] = sorted(name for name in directories if name not in ignored)
        for name in sorted(names):
            path = current_path / name
            relative = path.relative_to(root)
            if any(part in ignored for part in relative.parts):
                continue
            if path.is_symlink():
                _resolved_inside(path, root)
                continue
            if not path.is_file():
                continue
            stat = path.stat()
            if len(files) >= limits.max_snapshot_files:
                raise ValueError("The validation snapshot exceeded the configured file-count limit.")
            if total_bytes + stat.st_size > limits.max_snapshot_bytes:
                raise ValueError("The validation snapshot exceeded the configured size limit.")
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            files.append(SnapshotFile(
                relative_path=relative.as_posix(), size_bytes=stat.st_size,
                content_hash=digest.hexdigest(), modified_ns=stat.st_mtime_ns,
            ))
            total_bytes += stat.st_size
    files.sort(key=lambda item: item.relative_path.casefold())
    branch, commit, dirty = _git_metadata(root)
    snapshot_id = f"snapshot-{uuid4().hex}"
    backup_root: Path | None = None
    if create_backup:
        backup_root = root.parent / ".astra_validation_snapshots" / workspace.root_fingerprint[:16] / campaign_id / snapshot_id
        backup_root.mkdir(parents=True, exist_ok=False)
        for item in files:
            source_path = _resolved_inside(root / item.relative_path, root)
            target_path = backup_root / item.relative_path
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, target_path)
            if hashlib.sha256(target_path.read_bytes()).hexdigest() != item.content_hash:
                raise OSError(f"Snapshot backup verification failed for {item.relative_path}.")
    return BaselineSnapshot(
        snapshot_id=snapshot_id, campaign_id=campaign_id,
        workspace_id=workspace.workspace_id, file_manifest=files,
        directory_hash=stable_hash([item.model_dump(mode="json") for item in files]),
        file_count=len(files), total_bytes=total_bytes,
        git_branch=branch, git_commit=commit, dirty_worktree=dirty,
        exclusions=sorted(ignored), captured_at=datetime.now(timezone.utc),
        restorable=backup_root is not None, backup_root=str(backup_root) if backup_root else None,
    )


def compare_snapshot(snapshot: BaselineSnapshot, workspace: WorkspaceReference, limits: ValidationLimits) -> dict[str, list[str] | int | bool]:
    current = capture_snapshot(workspace, campaign_id=snapshot.campaign_id, limits=limits, exclusions=set(snapshot.exclusions), create_backup=False)
    before = {item.relative_path: item for item in snapshot.file_manifest}
    after = {item.relative_path: item for item in current.file_manifest}
    created = sorted(set(after) - set(before))
    deleted = sorted(set(before) - set(after))
    modified = sorted(path for path in set(before) & set(after) if before[path].content_hash != after[path].content_hash)
    changed_bytes = sum(after[path].size_bytes for path in created + modified if path in after) + sum(before[path].size_bytes for path in deleted)
    return {
        "created": created, "deleted": deleted, "modified": modified,
        "changed_bytes": changed_bytes, "stale": current.directory_hash != snapshot.directory_hash,
        "current_directory_hash": current.directory_hash,
    }


def restore_snapshot(snapshot: BaselineSnapshot, workspace: WorkspaceReference, *, remove_created: bool = True) -> dict[str, list[str] | bool]:
    if snapshot.workspace_id != workspace.workspace_id:
        raise WorkspaceSecurityError("The snapshot belongs to a different validation workspace.")
    root = Path(workspace.validation_root).resolve(strict=True)
    source_root = Path(workspace.source_root).resolve(strict=True)
    if workspace.root_fingerprint != root_fingerprint(source_root, authorization_id=workspace.authorization_id, conversation_id=workspace.conversation_id):
        raise WorkspaceSecurityError("The workspace authorization fingerprint no longer matches.")
    if not snapshot.restorable or not snapshot.backup_root:
        raise WorkspaceSecurityError("This snapshot does not have a restorable local backup.")
    backup = Path(snapshot.backup_root).resolve(strict=True)
    expected_parent = (root.parent / ".astra_validation_snapshots" / workspace.root_fingerprint[:16] / snapshot.campaign_id).resolve(strict=True)
    try:
        backup.relative_to(expected_parent)
    except ValueError as error:
        raise WorkspaceSecurityError("The snapshot backup location is not bound to this campaign workspace.") from error
    manifest = {item.relative_path: item for item in snapshot.file_manifest}
    restored: list[str] = []
    removed: list[str] = []
    failed: list[str] = []
    if remove_created:
        current = capture_snapshot(
            workspace, campaign_id=snapshot.campaign_id, limits=ValidationLimits(
                max_snapshot_files=max(1, max(len(manifest) * 4, 5000)),
                max_snapshot_bytes=max(1, max(snapshot.total_bytes * 4, 100_000_000)),
            ), exclusions=set(snapshot.exclusions), create_backup=False,
        )
        for relative in sorted(set(item.relative_path for item in current.file_manifest) - set(manifest), reverse=True):
            try:
                candidate = _resolved_inside(root / relative, root)
                if candidate.is_file() or candidate.is_symlink():
                    candidate.unlink()
                    removed.append(relative)
            except (OSError, WorkspaceSecurityError):
                failed.append(relative)
    for relative, item in manifest.items():
        try:
            source = _resolved_inside(backup / relative, backup)
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() and target.is_symlink():
                raise WorkspaceSecurityError("A restore target is a symbolic link.")
            shutil.copy2(source, target)
            digest = hashlib.sha256(target.read_bytes()).hexdigest()
            if digest != item.content_hash:
                raise OSError("restored hash mismatch")
            restored.append(relative)
        except (OSError, WorkspaceSecurityError):
            failed.append(relative)
    return {"restored": sorted(restored), "removed": sorted(removed), "failed": sorted(set(failed)), "complete": not failed}


__all__ = [
    "DEFAULT_EXCLUSIONS", "WorkspaceSecurityError", "capture_snapshot", "compare_snapshot",
    "prepare_workspace", "restore_snapshot", "root_fingerprint", "validate_authorized_root",
]
