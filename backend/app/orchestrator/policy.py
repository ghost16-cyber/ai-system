from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_IGNORE_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    ".continue",
    "env",
    "__pycache__",
    "build",
    "data",
    "dist",
    "legacy_archive",
    "node_modules",
    "site-packages",
    "venv",
}

DEFAULT_IGNORE_DIR_PREFIXES = (
    ".venv",
    "venv",
)

READABLE_EXTENSIONS = {
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".md",
    ".txt",
    ".ini",
    ".cfg",
}

PATCHABLE_EXTENSIONS = {".py"}

BLOCKED_FILENAMES = {
    ".env",
    ".env.local",
    ".env.production",
    "credentials.json",
}

BLOCKED_SUFFIXES = {
    ".db",
    ".log",
    ".pyc",
    ".pyo",
    ".sqlite",
    ".sqlite3",
    ".pem",
    ".key",
    ".p12",
    ".pfx",
}

ALLOWED_COMMANDS: dict[str, list[str]] = {
    "python -m pytest": ["python", "-m", "pytest"],
    "pytest": ["python", "-m", "pytest"],
    "python -m pytest -q": ["python", "-m", "pytest", "-q"],
    "python -m compileall .": ["python", "-m", "compileall", "."],
}


@dataclass(frozen=True)
class ResolvedPath:
    absolute: Path
    relative: str


class PolicyError(ValueError):
    """Raised when a proposed action violates orchestrator policy."""


class SafetyPolicy:
    def __init__(self, workspace_root: str | Path, project_path: str = ".") -> None:
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        requested_project = Path(project_path)
        if requested_project.is_absolute():
            raise PolicyError("Project path must be relative to the workspace root.")
        self.project_root = (self.workspace_root / requested_project).resolve()
        self._ensure_inside_workspace(self.project_root)
        if not self.project_root.exists():
            raise PolicyError("Project path does not exist.")
        if not self.project_root.is_dir():
            raise PolicyError("Project path must be a directory.")

    def resolve_read_path(self, path: str) -> ResolvedPath:
        resolved = self._resolve(path)
        self._ensure_readable_file(resolved.absolute)
        return resolved

    def resolve_patch_path(self, path: str) -> ResolvedPath:
        resolved = self._resolve(path)
        self._ensure_readable_file(resolved.absolute)
        if resolved.absolute.suffix.lower() not in PATCHABLE_EXTENSIONS:
            raise PolicyError("Only Python files can be patched in the MVP.")
        return resolved

    def resolve_project_path(self, path: str = ".") -> ResolvedPath:
        requested = Path(path)
        if requested.is_absolute():
            raise PolicyError("Tool paths must be relative to the task project path.")
        resolved = (self.project_root / requested).resolve()
        self._ensure_inside_workspace(resolved)
        try:
            resolved.relative_to(self.project_root)
        except ValueError as error:
            raise PolicyError("Tool path must stay inside the task project path.") from error
        return ResolvedPath(
            absolute=resolved,
            relative=resolved.relative_to(self.project_root).as_posix(),
        )

    def command_args(self, command: str) -> list[str]:
        normalized = " ".join(command.strip().split())
        try:
            return ALLOWED_COMMANDS[normalized]
        except KeyError as error:
            raise PolicyError(f"Command is not allowlisted: {command}") from error

    def is_ignored(self, path: Path) -> bool:
        try:
            parts = path.relative_to(self.project_root).parts
        except ValueError:
            return True
        if any(part in DEFAULT_IGNORE_DIRS for part in parts):
            return True
        if any(
            part.startswith(prefix)
            for part in parts
            for prefix in DEFAULT_IGNORE_DIR_PREFIXES
        ):
            return True
        if path.is_file() and path.suffix.lower() in BLOCKED_SUFFIXES:
            return True
        return False

    def public_relative(self, path: Path) -> str:
        return path.resolve().relative_to(self.workspace_root).as_posix()

    def task_relative(self, path: Path) -> str:
        return path.resolve().relative_to(self.project_root).as_posix()

    def _resolve(self, path: str) -> ResolvedPath:
        requested = Path(path.strip())
        if requested.is_absolute():
            raise PolicyError("Tool paths must be relative to the task project path.")
        resolved = (self.project_root / requested).resolve()
        self._ensure_inside_workspace(resolved)
        try:
            resolved.relative_to(self.project_root)
        except ValueError as error:
            raise PolicyError("Tool path must stay inside the task project path.") from error
        return ResolvedPath(
            absolute=resolved,
            relative=resolved.relative_to(self.project_root).as_posix(),
        )

    def _ensure_inside_workspace(self, path: Path) -> None:
        try:
            path.relative_to(self.workspace_root)
        except ValueError as error:
            raise PolicyError("Path must stay within the configured workspace root.") from error

    def _ensure_readable_file(self, path: Path) -> None:
        if self.is_ignored(path):
            raise PolicyError("Path is inside an ignored directory.")
        if not path.exists():
            raise FileNotFoundError("Requested file was not found.")
        if not path.is_file():
            raise PolicyError("Requested path is not a file.")
        if _is_blocked_file(path):
            raise PolicyError("Requested file is blocked by safety policy.")
        if path.suffix.lower() not in READABLE_EXTENSIONS:
            raise PolicyError("File extension is not readable by the orchestrator.")


def validate_patch_scope(args: dict[str, Any], max_changed_lines: int = 20) -> dict[str, Any]:
    old = str(args.get("old", ""))
    new = str(args.get("new", ""))
    if not old:
        return {"valid": False, "reason": "Patch old text must be non-empty."}
    old_lines = old.splitlines()
    new_lines = new.splitlines()
    changed_line_budget = max(len(old_lines), len(new_lines))
    if changed_line_budget > max_changed_lines:
        return {
            "valid": False,
            "reason": f"Patch exceeds the {max_changed_lines}-line limit.",
            "changed_line_budget": changed_line_budget,
            "max_changed_lines": max_changed_lines,
        }
    return {
        "valid": True,
        "changed_line_budget": changed_line_budget,
        "max_changed_lines": max_changed_lines,
        "old_length": len(old),
        "new_length": len(new),
    }


def _is_blocked_file(path: Path) -> bool:
    name = path.name.lower()
    suffix = path.suffix.lower()
    if name in BLOCKED_FILENAMES:
        return True
    if suffix in BLOCKED_SUFFIXES:
        return True
    return any(token in name for token in ("secret", "password", "credential"))
