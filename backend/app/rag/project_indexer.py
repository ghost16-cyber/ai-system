from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SAFE_PROJECT_EXTENSIONS = {
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".json",
    ".md",
    ".txt",
    ".css",
    ".html",
    ".toml",
    ".yaml",
    ".yml",
}

EXCLUDED_PATHS = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    "dist",
    "build",
    "coverage",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "data/specialists",
    "models",
    "outputs",
    ".env",
    ".env.local",
    "package-lock.json",
}

CHUNK_LINES = 80
CHUNK_OVERLAP = 15
INDEX_RELATIVE_PATH = Path("data/rag/project_index.json")
SECRET_MARKERS = ("api_key", "apikey", "secret=", "token=", "private key", "password=")


def build_project_index(workspace_root: str | Path) -> dict[str, Any]:
    root = resolve_project_root(workspace_root)
    chunks: list[dict[str, Any]] = []
    files: dict[str, dict[str, Any]] = {}
    skipped_files = 0

    for path in _iter_project_files(root):
        relative = path.relative_to(root).as_posix()
        text = _safe_read(path)
        if text is None:
            skipped_files += 1
            continue
        file_chunks = _chunk_file(relative, text)
        if not file_chunks:
            skipped_files += 1
            continue
        chunks.extend(file_chunks)
        files[relative] = {
            "path": relative,
            "chunk_count": len(file_chunks),
            "hash": _hash_text(text),
        }

    created_at = datetime.now(timezone.utc).isoformat()
    index = {
        "version": 1,
        "root": str(root),
        "created_at": created_at,
        "files": sorted(files.values(), key=lambda item: item["path"]),
        "chunks": chunks,
        "indexed_files": len(files),
        "indexed_chunks": len(chunks),
        "skipped_files": skipped_files,
        "safe_extensions": sorted(SAFE_PROJECT_EXTENSIONS),
        "exclusions": sorted(EXCLUDED_PATHS),
    }
    path = _index_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(index, indent=2, sort_keys=True), encoding="utf-8")
    return _index_summary(index)


def project_index_status(workspace_root: str | Path) -> dict[str, Any]:
    root = resolve_project_root(workspace_root)
    index = load_project_index(root)
    if index is None:
        return {
            "exists": False,
            "status": "index_missing",
            "root": str(root),
            "created_at": None,
            "indexed_files": 0,
            "indexed_chunks": 0,
        }
    return {
        "exists": True,
        "status": "ready",
        **_index_summary(index),
    }


def list_indexed_files(workspace_root: str | Path) -> dict[str, Any]:
    root = resolve_project_root(workspace_root)
    index = load_project_index(root)
    if index is None:
        return {
            "status": "index_missing",
            "root": str(root),
            "items": [],
            "count": 0,
        }
    files = index.get("files") if isinstance(index, dict) else []
    if not isinstance(files, list):
        files = []
    return {
        "status": "ready",
        "root": str(root),
        "items": files,
        "count": len(files),
    }


def search_project_index(
    workspace_root: str | Path,
    *,
    query: str,
    limit: int = 5,
    source_filter: str | None = None,
) -> dict[str, Any]:
    root = resolve_project_root(workspace_root)
    index = load_project_index(root)
    if index is None:
        return _search_response(
            query=query,
            root=root,
            results=[],
            status="index_missing",
        )

    terms = _query_terms(query)
    results: list[dict[str, Any]] = []
    chunks = index.get("chunks") if isinstance(index, dict) else []
    if not isinstance(chunks, list):
        chunks = []
    for chunk in chunks:
        if not isinstance(chunk, dict):
            continue
        path = str(chunk.get("path") or "")
        if source_filter and source_filter not in path:
            continue
        score = _score_chunk(chunk, terms)
        if score <= 0 and terms:
            continue
        text = str(chunk.get("text") or "")
        results.append(
            {
                "source": "project_index",
                "title": Path(path).name,
                "path": path,
                "start_line": int(chunk.get("start_line") or 1),
                "end_line": int(chunk.get("end_line") or 1),
                "score": float(score if terms else 0.1),
                "snippet": _snippet(text, terms),
            }
        )
    results = sorted(
        results,
        key=lambda item: (-float(item["score"]), str(item["path"]), int(item["start_line"])),
    )[: max(0, min(limit, 20))]
    return _search_response(query=query, root=root, results=results, status="ready")


def load_project_index(workspace_root: str | Path) -> dict[str, Any] | None:
    root = resolve_project_root(workspace_root)
    path = _index_path(root)
    try:
        index = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(index, dict):
        return None
    if index.get("root") and str(index["root"]) != str(root):
        return None
    return index


def resolve_project_root(workspace_root: str | Path) -> Path:
    configured = os.getenv("ASTRA_PROJECT_ROOT")
    root = Path(configured) if configured else Path(workspace_root)
    return root.expanduser().resolve()


def _iter_project_files(root: Path):
    if not root.exists():
        return
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if _is_excluded(relative):
            continue
        if path.suffix.lower() not in SAFE_PROJECT_EXTENSIONS:
            continue
        yield path


def _chunk_file(relative: str, text: str) -> list[dict[str, Any]]:
    lines = text.splitlines()
    if not lines:
        return []
    chunks: list[dict[str, Any]] = []
    step = max(1, CHUNK_LINES - CHUNK_OVERLAP)
    for start_index in range(0, len(lines), step):
        chunk_lines = lines[start_index : start_index + CHUNK_LINES]
        if not chunk_lines:
            continue
        chunk_text = "\n".join(chunk_lines)
        start_line = start_index + 1
        end_line = start_index + len(chunk_lines)
        chunks.append(
            {
                "path": relative,
                "start_line": start_line,
                "end_line": end_line,
                "text": chunk_text,
                "hash": _hash_text(f"{relative}:{start_line}:{end_line}:{chunk_text}"),
            }
        )
        if end_line >= len(lines):
            break
    return chunks


def _safe_read(path: Path) -> str | None:
    try:
        if path.stat().st_size > 750_000:
            return None
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    if _looks_sensitive(text[:4000]):
        return None
    return text


def _is_excluded(relative: str) -> bool:
    path = Path(relative)
    parts = path.parts
    normalized = relative.replace("\\", "/")
    if normalized == INDEX_RELATIVE_PATH.as_posix():
        return True
    if normalized in EXCLUDED_PATHS:
        return True
    if any(part in EXCLUDED_PATHS for part in parts):
        return True
    if any(part.startswith(".env") for part in parts):
        return True
    return any(normalized == excluded or normalized.startswith(f"{excluded}/") for excluded in EXCLUDED_PATHS)


def _score_chunk(chunk: dict[str, Any], terms: list[str]) -> float:
    if not terms:
        return 0.1
    text = f"{chunk.get('path') or ''}\n{chunk.get('text') or ''}".lower()
    counts = Counter(_tokenize(text))
    score = 0.0
    for term in terms:
        score += counts.get(term, 0) * 2
        if term in text:
            score += 0.5
    return score


def _query_terms(query: str) -> list[str]:
    return [term for term in _tokenize(query) if len(term) > 2]


def _tokenize(text: str) -> list[str]:
    normalized = "".join(character.lower() if character.isalnum() else " " for character in text)
    return normalized.split()


def _snippet(text: str, terms: list[str], max_chars: int = 520) -> str:
    lowered = text.lower()
    start = 0
    for term in terms:
        index = lowered.find(term)
        if index >= 0:
            start = max(0, index - 120)
            break
    return " ".join(text[start : start + max_chars].split())


def _search_response(
    *,
    query: str,
    root: Path,
    results: list[dict[str, Any]],
    status: str,
) -> dict[str, Any]:
    return {
        "query": query,
        "status": status,
        "root": str(root),
        "results": results,
        "count": len(results),
        "advisory_only": True,
        "tools_executed": False,
        "patches_applied": False,
        "runtime_authorized": False,
    }


def _index_summary(index: dict[str, Any]) -> dict[str, Any]:
    return {
        "root": str(index.get("root") or ""),
        "created_at": index.get("created_at"),
        "indexed_files": int(index.get("indexed_files") or 0),
        "indexed_chunks": int(index.get("indexed_chunks") or 0),
        "skipped_files": int(index.get("skipped_files") or 0),
    }


def _index_path(root: Path) -> Path:
    return root / INDEX_RELATIVE_PATH


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _looks_sensitive(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in SECRET_MARKERS)
