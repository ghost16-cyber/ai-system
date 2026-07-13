from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


PathExpectation = Literal["file", "directory", "any"]

WINDOWS_DRIVE_RE = re.compile(r"^(?P<drive>[a-zA-Z]):[\\/](?P<rest>.*)$")
WSL_UNC_RE = re.compile(
    r"^[\\/]{2}wsl(?:\.localhost)?[\\/](?P<distribution>[^\\/]+)[\\/](?P<rest>.*)$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class NormalizedPath:
    raw_path: str
    path: Path
    windows_path_detected: bool = False
    suggested_path: str | None = None


def is_windows_path(value: str | os.PathLike[str]) -> bool:
    return bool(WINDOWS_DRIVE_RE.match(str(value).strip()))


def windows_path_to_wsl(value: str | os.PathLike[str]) -> str:
    raw = str(value).strip()
    match = WINDOWS_DRIVE_RE.match(raw)
    if not match:
        return raw
    drive = match.group("drive").lower()
    rest = match.group("rest").replace("\\", "/")
    return f"/mnt/{drive}/{rest}"


def wsl_unc_path_to_linux(value: str | os.PathLike[str]) -> str:
    """Translate a WSL share path to its in-distribution absolute Linux path."""
    raw = str(value).strip()
    match = WSL_UNC_RE.match(raw)
    if not match:
        return raw
    rest = match.group("rest").replace("\\", "/").lstrip("/")
    return f"/{rest}"


def normalize_path_for_platform(value: str | os.PathLike[str]) -> NormalizedPath:
    raw = str(value).strip()
    if WSL_UNC_RE.match(raw) and os.name != "nt":
        suggested = wsl_unc_path_to_linux(raw)
        return NormalizedPath(
            raw_path=raw,
            path=Path(suggested).expanduser(),
            windows_path_detected=True,
            suggested_path=suggested,
        )
    if is_windows_path(raw) and os.name != "nt":
        suggested = windows_path_to_wsl(raw)
        return NormalizedPath(
            raw_path=raw,
            path=Path(suggested).expanduser(),
            windows_path_detected=True,
            suggested_path=suggested,
        )
    return NormalizedPath(raw_path=raw, path=Path(raw).expanduser(), windows_path_detected=is_windows_path(raw))


def resolve_user_path(
    raw_path: str | os.PathLike[str] | None,
    *,
    base_root: str | Path,
    allowed_roots: list[str | Path] | tuple[str | Path, ...] | None = None,
    expected: PathExpectation = "any",
    supported_extensions: set[str] | frozenset[str] | None = None,
    label: str = "Path",
    require_exists: bool = True,
) -> Path:
    if raw_path is None or str(raw_path).strip() == "":
        return Path(base_root).expanduser().resolve()

    normalized = normalize_path_for_platform(raw_path)
    if _has_traversal(normalized.raw_path):
        raise ValueError("Unsafe path traversal attempt: paths cannot include '..'.")

    base = Path(base_root).expanduser().resolve()
    candidate = normalized.path
    resolved = candidate.resolve() if candidate.is_absolute() else (base / candidate).resolve()

    roots = [Path(root).expanduser().resolve() for root in (allowed_roots or [base])]
    if not _is_under_any_root(resolved, roots):
        extra = ""
        if normalized.windows_path_detected and normalized.suggested_path:
            extra = f" Windows path detected; WSL equivalent is {normalized.suggested_path}."
        raise ValueError(
            f"{label} is outside the allowed workspace root.{extra} "
            "Copy the file into assignment_inputs or assignment_workspaces, or use an allowed root."
        )

    if require_exists and not resolved.exists():
        extra = ""
        if normalized.windows_path_detected and normalized.suggested_path:
            extra = f" Windows path detected; try the WSL path {normalized.suggested_path}."
        raise FileNotFoundError(f"{label} not found: {resolved}.{extra}")

    if require_exists and expected == "file" and not resolved.is_file():
        raise ValueError(f"{label} must point to a file, but this path is a folder: {resolved}")
    if require_exists and expected == "directory" and not resolved.is_dir():
        raise ValueError(f"{label} must point to a folder, but this path is a file: {resolved}")

    if supported_extensions is not None and resolved.suffix.lower() not in supported_extensions:
        supported = ", ".join(sorted(supported_extensions))
        raise ValueError(f"Unsupported {label.lower()} extension: {resolved.suffix.lower()}. Supported extensions: {supported}.")

    return resolved


def _has_traversal(raw_path: str) -> bool:
    return any(part == ".." for part in re.split(r"[\\/]+", raw_path))


def _is_under_any_root(path: Path, roots: list[Path]) -> bool:
    for root in roots:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            continue
    return False
