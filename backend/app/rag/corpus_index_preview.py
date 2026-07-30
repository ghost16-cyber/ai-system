from __future__ import annotations

import math
from collections import Counter
from pathlib import Path
from typing import Any

from backend.app.rag.corpus_inventory import (
    CHUNK_TARGET_BYTES,
    DEFAULT_CORPUS_ROOT,
    scan_corpus,
)


def _estimate_file_chunks(size_bytes: int, index_eligible: bool) -> int:
    """Estimate chunks without reading file contents or creating embeddings."""
    if not index_eligible or size_bytes <= 0:
        return 0

    return max(1, math.ceil(size_bytes / CHUNK_TARGET_BYTES))


def build_corpus_index_preview(
    root: str | Path = DEFAULT_CORPUS_ROOT,
) -> dict[str, Any]:
    """
    Build a deterministic dry-run preview of the corpus index.

    This function:
    - does not read file contents;
    - does not create embeddings;
    - does not write an index;
    - does not execute corpus code.
    """
    inventory = scan_corpus(root)

    indexable_files: list[dict[str, Any]] = []
    excluded_files: list[dict[str, Any]] = []
    exclusion_reason_counts: Counter[str] = Counter()

    for item in inventory["files"]:
        accepted = bool(item.get("accepted", False))
        index_eligible = bool(item.get("index_eligible", False))
        size_bytes = int(item.get("size_bytes", 0))

        estimated_chunks = _estimate_file_chunks(
            size_bytes,
            index_eligible,
        )

        preview_record = {
            "relative_path": str(item.get("relative_path", "")),
            "extension": str(item.get("extension", "")),
            "size_bytes": size_bytes,
            "accepted": accepted,
            "inventory_reason": str(item.get("reason", "")),
            "index_eligible": index_eligible,
            "index_reason": str(
                item.get("index_reason", "inventory_rejected")
            ),
            "estimated_chunks": estimated_chunks,
        }

        if index_eligible:
            indexable_files.append(preview_record)
            continue

        exclusion_reason = (
            preview_record["index_reason"]
            if accepted
            else preview_record["inventory_reason"]
        )

        preview_record["exclusion_reason"] = exclusion_reason
        excluded_files.append(preview_record)
        exclusion_reason_counts[exclusion_reason] += 1

    estimated_index_chunk_count = sum(
        item["estimated_chunks"] for item in indexable_files
    )

    return {
        "root": inventory["root"],
        "corpus_exists": inventory["corpus_exists"],
        "mode": "dry_run",
        "writes_performed": False,
        "embeddings_created": False,
        "total_files": inventory["total_files"],
        "indexable_files": len(indexable_files),
        "index_skipped_files": inventory["index_skipped_files"],
        "ignored_files": inventory["ignored_files"],
        "excluded_file_count": len(excluded_files),
        "estimated_index_chunk_count": estimated_index_chunk_count,
        "indexable_type_counts": inventory["indexable_type_counts"],
        "index_skip_reason_counts": inventory[
            "index_skip_reason_counts"
        ],
        "exclusion_reason_counts": dict(
            sorted(exclusion_reason_counts.items())
        ),
        "files": indexable_files,
        "excluded_files": excluded_files,
    }
