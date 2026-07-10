from __future__ import annotations

from collections import OrderedDict
from pathlib import Path

from backend.app.core.path_utils import normalize_path_for_platform
from backend.app.workspace.schemas import WorkspaceInspection


SKIPPED_DIRECTORIES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "dist",
    "build",
    "node_modules",
}
SECRET_FILE_NAMES = {
    ".env",
    ".env.local",
    ".env.production",
    "credentials.json",
    "secrets.json",
    "id_rsa",
    "id_ed25519",
}
SECRET_NAME_PARTS = ("secret", "credential", "private_key", "apikey", "api_key")
LARGE_DATA_SUFFIXES = {".csv", ".parquet", ".avro", ".orc", ".sqlite", ".db", ".gz", ".zip"}
LANGUAGE_BY_SUFFIX = {
    ".py": "Python",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".html": "HTML",
    ".css": "CSS",
    ".sql": "SQL",
    ".sh": "Shell",
    ".md": "Markdown",
    ".yml": "YAML",
    ".yaml": "YAML",
}
RECOMMENDED_FILES = ("README.md", "requirements.txt", "report_outline.md")
IMPORTANT_NAMES = {
    "docker-compose.yml",
    "docker-compose.yaml",
    "producer.py",
    "consumer.py",
    "consumer_to_influx.py",
    "spark_processing.py",
    "structured_streaming_job.py",
    "snowflake_loader.py",
    "requirements.txt",
    "README.md",
    "report_outline.md",
}


def inspect_workspace(
    root_path: str | Path,
    *,
    max_files: int = 250,
    max_preview_bytes: int = 4000,
    max_file_size: int = 256_000,
) -> WorkspaceInspection:
    root = normalize_path_for_platform(root_path).path.expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError(f"Workspace path not found: {root}")
    if not root.is_dir():
        raise ValueError("Workspace inspection path must be a directory.")

    detected_files: list[str] = []
    detected_directories: list[str] = []
    warnings: list[str] = []
    languages: list[str] = []
    tools: list[str] = []
    important: list[str] = []
    scanned = 0
    skipped = 0

    for current, dir_names, file_names in _walk_sorted(root):
        kept_dirs = []
        for directory in dir_names:
            if directory in SKIPPED_DIRECTORIES:
                skipped += 1
                continue
            kept_dirs.append(directory)
            detected_directories.append(_relative(root, current / directory))
        dir_names[:] = kept_dirs

        for file_name in file_names:
            file_path = current / file_name
            relative = _relative(root, file_path)
            if _is_secret_path(file_path):
                skipped += 1
                warnings.append(f"Skipped secret-like file: {relative}")
                continue
            if file_path.suffix.lower() in LARGE_DATA_SUFFIXES:
                skipped += 1
                warnings.append(f"Skipped data file: {relative}")
                continue
            if scanned >= max_files:
                skipped += 1
                continue
            try:
                size = file_path.stat().st_size
            except OSError:
                skipped += 1
                continue
            if size > max_file_size:
                skipped += 1
                warnings.append(f"Skipped large file: {relative}")
                continue

            scanned += 1
            detected_files.append(relative)
            suffix = file_path.suffix.lower()
            if suffix in LANGUAGE_BY_SUFFIX:
                languages.append(LANGUAGE_BY_SUFFIX[suffix])
            if _is_important(relative):
                important.append(relative)
            preview = _safe_preview(file_path, max_preview_bytes=max_preview_bytes)
            tools.extend(_detect_tools(relative, preview))

    missing = [name for name in RECOMMENDED_FILES if name not in {Path(item).name for item in detected_files}]
    return WorkspaceInspection(
        root_path=str(root),
        detected_files=_unique_sorted(detected_files),
        detected_directories=_unique_sorted(detected_directories),
        detected_languages=_unique_sorted(languages),
        detected_frameworks_tools=_unique_sorted(tools),
        important_files=_unique_sorted(important),
        missing_recommended_files=missing,
        warnings=_unique(warnings),
        files_scanned=scanned,
        files_skipped=skipped,
    )


def _walk_sorted(root: Path):
    for current, dir_names, file_names in root.walk():
        dir_names.sort()
        file_names.sort()
        yield current, dir_names, file_names


def _relative(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root).as_posix()


def _is_secret_path(path: Path) -> bool:
    name = path.name.lower()
    if name in SECRET_FILE_NAMES:
        return True
    return any(part in name for part in SECRET_NAME_PARTS)


def _is_important(relative_path: str) -> bool:
    path = Path(relative_path)
    if path.name in IMPORTANT_NAMES:
        return True
    lowered = relative_path.lower()
    return (
        "spark" in lowered
        or "streamlit" in lowered
        or "snowflake" in lowered
        or "redis" in lowered
        or "report" in lowered
    )


def _safe_preview(path: Path, *, max_preview_bytes: int) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")[:max_preview_bytes]
    except UnicodeDecodeError:
        return ""
    except OSError:
        return ""


def _detect_tools(relative_path: str, preview: str) -> list[str]:
    lowered_path = relative_path.lower()
    lowered_preview = preview.lower()
    combined = f"{lowered_path}\n{lowered_preview}"
    detected: list[str] = []
    if Path(relative_path).name in {"docker-compose.yml", "docker-compose.yaml"}:
        detected.append("Docker Compose")
    if "kafka" in combined:
        detected.append("Kafka")
    if "influxdb" in combined or "influx" in combined:
        detected.append("InfluxDB")
    if "grafana" in combined:
        detected.append("Grafana")
    if "pyspark" in combined or "spark" in combined:
        detected.append("PySpark")
    if "snowflake" in combined:
        detected.append("Snowflake")
    if "streamlit" in combined:
        detected.append("Streamlit")
    if "redis" in combined:
        detected.append("Redis")
    if "package.json" in lowered_path:
        detected.append("Node/npm")
    if "vite" in combined:
        detected.append("Vite")
    if "react" in combined:
        detected.append("React")
    return detected


def _unique(values) -> list[str]:
    ordered = OrderedDict()
    for value in values:
        cleaned = str(value).strip()
        if cleaned:
            ordered.setdefault(cleaned, None)
    return list(ordered)


def _unique_sorted(values) -> list[str]:
    return sorted(_unique(values))
