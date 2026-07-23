from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath

from backend.app.project_control.contracts import content_hash


ALLOWED_EXTENSIONS = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".json", ".yaml", ".yml", ".toml",
    ".ini", ".cfg", ".md", ".rst", ".txt", ".sql", ".sh", ".ps1", ".html",
    ".css", ".xml",
}
EXCLUDED_PARTS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", "dist", "build",
    "coverage", ".coverage", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".cache", "models",
}
EXCLUDED_NAMES = {
    ".env", ".env.local", ".env.production", ".env.development",
    "id_rsa", "id_ed25519", "credentials.json", "secrets.json",
}


class RetrievalPathError(ValueError):
    pass


def normalize_relative_path(value: str) -> str:
    raw = value.replace("\\", "/").strip()
    path = PurePosixPath(raw)
    if not raw or path.is_absolute() or ".." in path.parts:
        raise RetrievalPathError("invalid_relative_path")
    normalized = path.as_posix()
    if normalized.startswith("/") or normalized in {".", ""}:
        raise RetrievalPathError("invalid_relative_path")
    return normalized


def resolve_safe_path(repository_root: Path, relative_path: str) -> Path:
    relative = normalize_relative_path(relative_path)
    root = repository_root.resolve(strict=True)
    candidate = (root / relative).resolve(strict=True)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise RetrievalPathError("path_escape") from exc
    if candidate.is_symlink() or not candidate.is_file():
        raise RetrievalPathError("invalid_source_file")
    return candidate


def eligible_relative_path(relative_path: str) -> bool:
    try:
        normalized = normalize_relative_path(relative_path)
    except RetrievalPathError:
        return False
    path = PurePosixPath(normalized)
    lowered = tuple(part.casefold() for part in path.parts)
    if any(part in EXCLUDED_PARTS for part in lowered):
        return False
    if path.name.casefold() in EXCLUDED_NAMES or path.name.casefold().startswith(".env"):
        return False
    if any(part in {"secrets", "keys", "private"} for part in lowered[:-1]):
        return False
    return path.suffix.casefold() in ALLOWED_EXTENSIONS


def path_in_scope(
    relative_path: str,
    included_paths: tuple[str, ...],
    excluded_paths: tuple[str, ...],
) -> bool:
    path = normalize_relative_path(relative_path)
    included = tuple(normalize_relative_path(item).rstrip("/") for item in included_paths)
    excluded = tuple(normalize_relative_path(item).rstrip("/") for item in excluded_paths)
    if included and not any(path == item or path.startswith(f"{item}/") for item in included):
        return False
    return not any(path == item or path.startswith(f"{item}/") for item in excluded)


def exact_bytes_hash(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def repository_state_hash(items: list[tuple[str, str]]) -> str:
    return content_hash(
        [{"relative_path": path, "content_hash": digest} for path, digest in sorted(items)]
    )

