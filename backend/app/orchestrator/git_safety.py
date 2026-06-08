from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DirtyWorktreeStatus:
    is_git_repo: bool
    git_root: str | None
    dirty: bool
    dirty_files: tuple[str, ...] = ()
    target_dirty: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "is_git_repo": self.is_git_repo,
            "git_root": self.git_root,
            "dirty": self.dirty,
            "dirty_files": list(self.dirty_files),
            "target_dirty": self.target_dirty,
        }


def get_dirty_worktree_status(
    project_root: Path,
    target_file: Path | None = None,
) -> DirtyWorktreeStatus:
    git_root = _git_root(project_root)
    if git_root is None:
        return DirtyWorktreeStatus(
            is_git_repo=False,
            git_root=None,
            dirty=False,
        )

    lines = _git_status_lines(git_root)
    dirty_files = tuple(_status_path(line) for line in lines if _status_path(line))
    target_dirty = False
    if target_file is not None:
        try:
            relative_target = target_file.resolve().relative_to(git_root).as_posix()
        except ValueError:
            relative_target = target_file.name
        target_dirty = relative_target in dirty_files

    return DirtyWorktreeStatus(
        is_git_repo=True,
        git_root=git_root.as_posix(),
        dirty=bool(dirty_files),
        dirty_files=dirty_files,
        target_dirty=target_dirty,
    )


def _git_root(project_root: Path) -> Path | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(project_root), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    root = completed.stdout.strip()
    return Path(root).resolve() if root else None


def _git_status_lines(git_root: Path) -> list[str]:
    completed = subprocess.run(
        ["git", "-C", str(git_root), "status", "--porcelain"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if completed.returncode != 0:
        return []
    return [line for line in completed.stdout.splitlines() if line.strip()]


def _status_path(line: str) -> str:
    if len(line) < 4:
        return ""
    path = line[3:].strip()
    if " -> " in path:
        path = path.split(" -> ", 1)[1].strip()
    return path.strip('"')
