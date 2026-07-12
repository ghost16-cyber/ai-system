from __future__ import annotations

import hashlib
import re
from pathlib import Path

from backend.app.folders.scanner import IGNORED_DIRS, file_ignore_reason, validate_folder_root


SAFE_TEXT_SUFFIXES = frozenset(
    {
        ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".c", ".cpp", ".h",
        ".hpp", ".cs", ".go", ".rs", ".php", ".rb", ".html", ".css", ".scss",
        ".sql", ".json", ".jsonl", ".yaml", ".yml", ".toml", ".ini", ".cfg",
        ".md", ".txt", ".sh", ".ps1", ".bat", ".dockerfile", ".xml", ".gradle",
        ".properties", ".lock",
    }
)
SAFE_TEXT_NAMES = frozenset(
    {
        "dockerfile", "makefile", "procfile", "gemfile", "rakefile", "package.json",
        "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "pyproject.toml",
        "requirements.txt", "pipfile", "pipfile.lock", "poetry.lock", "cargo.toml",
        "cargo.lock", "go.mod", "go.sum", "composer.json", "composer.lock",
    }
)
SENSITIVE_MARKERS = (
    "credential", "secret", "token", "private_key", "private-key", "id_rsa",
    "id_dsa", "id_ecdsa", "id_ed25519", "known_hosts", "authorized_keys",
    "keystore", "keychain", "cookies", "login data",
)


class ProjectSafetyError(ValueError):
    pass


def project_root_fingerprint(root: str | Path) -> str:
    approved = validate_folder_root(str(root))
    stat = approved.stat()
    identity = f"{approved}:{stat.st_dev}:{stat.st_ino}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def validate_root_identity(root: str | Path, expected_fingerprint: str) -> Path:
    approved = validate_folder_root(str(root))
    if project_root_fingerprint(approved) != expected_fingerprint:
        raise ProjectSafetyError("The approved project root identity has changed.")
    return approved


def safe_relative_path(value: str) -> str:
    raw = (value or "").strip().replace("\\", "/")
    if not raw or raw.startswith("/") or re.match(r"^[A-Za-z]:/", raw):
        raise ProjectSafetyError("Project paths must be relative to the approved root.")
    parts = [part for part in raw.split("/") if part not in {"", "."}]
    if not parts or any(part == ".." for part in parts):
        raise ProjectSafetyError("Project path traversal is not allowed.")
    return "/".join(parts)


def resolve_project_path(
    root: str | Path,
    relative_path: str,
    *,
    must_exist: bool = True,
) -> Path:
    approved = validate_folder_root(str(root))
    relative = safe_relative_path(relative_path)
    candidate = approved.joinpath(*relative.split("/"))
    current = approved
    for part in relative.split("/"):
        current = current / part
        if current.exists() or current.is_symlink():
            if current.is_symlink():
                raise ProjectSafetyError("Symlink project paths are not allowed.")
    resolved_parent = candidate.parent.resolve()
    try:
        resolved_parent.relative_to(approved)
    except ValueError as error:
        raise ProjectSafetyError("Project path escapes the approved root.") from error
    if must_exist and not candidate.exists():
        raise FileNotFoundError(f"Project file not found: {relative}")
    if must_exist and not candidate.is_file():
        raise ProjectSafetyError(f"Project path is not a regular file: {relative}")
    return candidate


def project_file_exclusion_reason(path: Path, root: Path, *, size: int | None = None) -> str | None:
    relative = path.relative_to(root).as_posix()
    components = [part.lower() for part in Path(relative).parts]
    if any(part in IGNORED_DIRS or part in {".svn", ".hg", "vendor"} for part in components[:-1]):
        return "excluded_directory"
    name = path.name.lower()
    if name == ".env" or name.startswith(".env."):
        return "sensitive_file"
    if any(marker in name for marker in SENSITIVE_MARKERS):
        return "sensitive_file"
    if name.endswith((".pem", ".key", ".p12", ".pfx", ".crt", ".cer")):
        return "sensitive_file"
    reason = file_ignore_reason(path, path.stat().st_size if size is None else size)
    if reason:
        return reason
    if name not in SAFE_TEXT_NAMES and path.suffix.lower() not in SAFE_TEXT_SUFFIXES:
        return "unsupported_file_type"
    return None


def is_safe_text_project_file(path: Path, root: Path) -> bool:
    try:
        return project_file_exclusion_reason(path, root) is None
    except (OSError, ValueError):
        return False
