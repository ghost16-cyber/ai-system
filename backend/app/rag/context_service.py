from __future__ import annotations

import json
from pathlib import Path
from typing import Any


SAFE_EXTENSIONS = {".md", ".txt", ".json", ".py"}
EXCLUDED_PARTS = {
    ".env",
    ".venv",
    "node_modules",
    "__pycache__",
    ".git",
    "dist",
    "build",
    "secrets",
    "keys",
}
SECRET_MARKERS = ("api_key", "apikey", "secret=", "token=", "private key", "password=")
DEFAULT_SOURCE_DIRS = ("docs", "system information", "backend/app/specialists")


def rag_status(workspace_root: str | Path) -> dict[str, Any]:
    root = Path(workspace_root).resolve()
    files = list(_iter_safe_files(root))
    return {
        "status": "ready",
        "workspace_root": str(root),
        "indexed_file_count": len(files),
        "source_roots": list(DEFAULT_SOURCE_DIRS),
        "safe_extensions": sorted(SAFE_EXTENSIONS),
        "exclusions": sorted(EXCLUDED_PARTS),
        "advisory_only": True,
        "tools_executed": False,
        "patches_applied": False,
        "runtime_authorized": False,
    }


def rag_search(
    workspace_root: str | Path,
    *,
    query: str,
    limit: int = 5,
    source_filter: str | None = None,
) -> dict[str, Any]:
    root = Path(workspace_root).resolve()
    terms = [term for term in query.lower().split() if len(term) > 2]
    results: list[dict[str, Any]] = []
    for path in _iter_safe_files(root):
        relative = path.relative_to(root).as_posix()
        if source_filter and source_filter not in relative:
            continue
        text = _safe_read(path)
        if not text:
            continue
        lowered = text.lower()
        score = sum(lowered.count(term) + relative.lower().count(term) for term in terms)
        if score <= 0 and terms:
            continue
        snippet = _snippet(text, terms)
        if _looks_sensitive(snippet):
            continue
        results.append(
            {
                "source": _source_for_path(relative),
                "title": path.name,
                "path": relative,
                "snippet": snippet,
                "score": float(score if terms else 0.1),
            }
        )
    results = sorted(results, key=lambda item: item["score"], reverse=True)[: max(0, min(limit, 20))]
    return {
        "query": query,
        "results": results,
        "count": len(results),
        "advisory_only": True,
        "tools_executed": False,
        "patches_applied": False,
        "runtime_authorized": False,
    }


def compact_context(results: list[dict[str, Any]], max_chars: int = 1600) -> str:
    chunks = [
        f"{item.get('path')}: {item.get('snippet')}"
        for item in results
        if item.get("snippet")
    ]
    return "\n".join(chunks)[:max_chars]


def _iter_safe_files(root: Path):
    for source in DEFAULT_SOURCE_DIRS:
        source_path = root / source
        if not source_path.exists():
            continue
        if source_path.is_file():
            candidates = [source_path]
        else:
            candidates = source_path.rglob("*")
        for path in candidates:
            if not path.is_file() or path.suffix.lower() not in SAFE_EXTENSIONS:
                continue
            relative_parts = set(path.relative_to(root).parts)
            if relative_parts & EXCLUDED_PARTS:
                continue
            if any(part.startswith(".env") for part in relative_parts):
                continue
            yield path


def _safe_read(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""
    if _looks_sensitive(text[:4000]):
        return ""
    return text[:12000]


def _snippet(text: str, terms: list[str]) -> str:
    lowered = text.lower()
    start = 0
    for term in terms:
        index = lowered.find(term)
        if index >= 0:
            start = max(0, index - 120)
            break
    return " ".join(text[start : start + 520].split())


def _looks_sensitive(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in SECRET_MARKERS)


def _source_for_path(relative: str) -> str:
    if relative.startswith("docs/"):
        return "docs"
    if relative.startswith("system information/"):
        return "reports"
    if "specialists" in relative:
        return "specialists"
    return "workspace"
