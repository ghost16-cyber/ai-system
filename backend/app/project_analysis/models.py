from __future__ import annotations

from typing import Any


INDEX_VERSION = "6.0"
MAX_INDEX_FILES = 160
MAX_INDEX_BYTES = 1_500_000
MAX_RELATIONSHIPS = 600
MAX_SYMBOLS_PER_FILE = 120
MAX_IMPACT_FILES = 10
MAX_SYNTHESIS_BYTES = 250_000


class ProjectAnalysisError(ValueError):
    pass


def source_range(node: Any) -> dict[str, int] | None:
    start = getattr(node, "lineno", None)
    if not isinstance(start, int):
        return None
    end = getattr(node, "end_lineno", start)
    return {"start_line": start, "end_line": int(end or start)}


def bounded_text(value: Any, limit: int = 500) -> str:
    return " ".join(str(value or "").split())[:limit]


def confidence_level(score: float) -> str:
    if score >= 0.78:
        return "high"
    if score >= 0.52:
        return "medium"
    return "low"


__all__ = [
    "INDEX_VERSION", "MAX_IMPACT_FILES", "MAX_INDEX_BYTES", "MAX_INDEX_FILES",
    "MAX_RELATIONSHIPS", "MAX_SYMBOLS_PER_FILE", "MAX_SYNTHESIS_BYTES",
    "ProjectAnalysisError", "bounded_text", "confidence_level", "source_range",
]
