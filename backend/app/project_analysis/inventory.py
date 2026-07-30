from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.app.folders.reader import ReadLimits, iter_project_files, read_project_file
from backend.app.folders.safety import project_root_fingerprint, safe_relative_path
from backend.app.project_analysis.dependencies import build_dependency_graph
from backend.app.project_analysis.languages import detect_language, is_structured_language
from backend.app.project_analysis.models import INDEX_VERSION, MAX_INDEX_BYTES, MAX_INDEX_FILES, ProjectAnalysisError
from backend.app.project_analysis.symbols import analyze_source


def build_project_index(
    root: str | Path,
    *,
    conversation_id: str,
    folder_access_id: str,
    job_id: str,
    previous: dict[str, Any] | None = None,
    max_files: int = MAX_INDEX_FILES,
    max_total_bytes: int = MAX_INDEX_BYTES,
    allow_parser_helper: bool = True,
) -> dict[str, Any]:
    approved = Path(root).resolve()
    previous_files = {str(item.get("relative_path")): item for item in (previous or {}).get("files", []) if isinstance(item, dict)}
    files: list[dict[str, Any]] = []
    current_paths: set[str] = set()
    total_bytes = 0
    added: list[str] = []
    changed: list[str] = []
    unchanged: list[str] = []
    failures: list[str] = []
    for path in iter_project_files(approved, max_files=max_files + 1):
        if len(current_paths) >= max_files:
            raise ProjectAnalysisError(f"Project structure exceeds the bounded {max_files}-file analysis limit; narrow the task.")
        relative = safe_relative_path(path.relative_to(approved).as_posix())
        current_paths.add(relative)
        language = detect_language(relative)
        if not is_structured_language(language):
            continue
        record = read_project_file(approved, relative, limits=ReadLimits(max_files=max_files, max_total_bytes=max_total_bytes))
        if record["status"] != "readable":
            failures.append(relative)
            files.append({"relative_path": relative, "language": language, "file_hash": None, "size_bytes": int(record.get("size_bytes") or 0), "parse_status": "excluded", "syntax_errors": [], "symbols": [], "imports": [], "exports": [], "calls": [], "routes": [], "tests": [], "warnings": [str(record.get("reason") or "excluded")]})
            continue
        total_bytes += int(record["bytes_read"])
        if total_bytes > max_total_bytes:
            raise ProjectAnalysisError(f"Project structure exceeds the bounded {max_total_bytes}-byte analysis limit; narrow the task.")
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        prior = previous_files.get(relative)
        if prior and prior.get("file_hash") == digest:
            item = dict(prior)
            unchanged.append(relative)
        else:
            analysis = analyze_source(relative, language, str(record["text"]), allow_parser_helper=allow_parser_helper)
            item = {"relative_path": relative, "language": language, "file_hash": digest, "size_bytes": int(record["size_bytes"]), **analysis}
            (changed if prior else added).append(relative)
            if analysis.get("parse_status") == "failed":
                failures.append(relative)
        files.append(item)
    removed = sorted(set(previous_files) - current_paths)
    relationships = build_dependency_graph(files)
    now = datetime.now(timezone.utc).isoformat()
    index = {
        "analysis_id": uuid4().hex,
        "index_version": INDEX_VERSION,
        "conversation_id": conversation_id,
        "folder_access_id": folder_access_id,
        "job_id": job_id,
        "root_fingerprint": project_root_fingerprint(approved),
        "created_at": now,
        "files": files,
        "relationships": relationships,
        "incremental": {"added": sorted(added), "changed": sorted(changed), "unchanged": sorted(unchanged), "removed": removed, "parse_failures": sorted(set(failures))},
        "limits": {"max_files": max_files, "max_total_bytes": max_total_bytes, "indexed_files": len(files), "indexed_bytes": total_bytes},
    }
    return index


def public_index(index: dict[str, Any]) -> dict[str, Any]:
    return {key: index.get(key) for key in ("analysis_id", "index_version", "conversation_id", "folder_access_id", "job_id", "created_at", "files", "relationships", "incremental", "limits")}


def file_hashes(index: dict[str, Any]) -> dict[str, str]:
    return {str(item["relative_path"]): str(item["file_hash"]) for item in index.get("files", []) if item.get("file_hash")}


__all__ = ["build_project_index", "file_hashes", "public_index"]
