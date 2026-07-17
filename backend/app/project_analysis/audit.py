from __future__ import annotations

from typing import Any


def analysis_audit_metadata(index: dict[str, Any], *, include_relationships: bool = False) -> dict[str, Any]:
    files = list(index.get("files") or [])
    metadata = {
        "analysis_id": index.get("analysis_id"), "index_version": index.get("index_version"),
        "relative_paths": [item.get("relative_path") for item in files[:160]],
        "file_count": len(files), "parse_failure_count": len(index.get("incremental", {}).get("parse_failures", [])),
    }
    if include_relationships:
        metadata["relationship_count"] = len(index.get("relationships") or [])
    return metadata


__all__ = ["analysis_audit_metadata"]
