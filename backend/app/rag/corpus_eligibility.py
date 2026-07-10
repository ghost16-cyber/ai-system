from __future__ import annotations

import math
from collections import Counter, defaultdict
from pathlib import PurePosixPath
from typing import Any


SUSPICIOUS_EXTENSIONS = {
    ".cvs",
    ".csv1",
}

GENERATED_TABULAR_SUFFIXES = {
    "_train",
    "_test",
    "_val",
    "_validation",
    "_prediction",
    "_predictions",
    "_training_history",
    "_threshold_tuning",
}

DUPLICATE_FORMAT_EXTENSIONS = {
    ".md",
    ".txt",
    ".json",
    ".pdf",
    ".docx",
}

DUPLICATE_FORMAT_PRIORITY = {
    ".md": 0,
    ".txt": 1,
    ".json": 2,
    ".pdf": 3,
    ".docx": 4,
}


def _is_generated_tabular_file(relative_path: str, extension: str) -> bool:
    if extension != ".csv":
        return False

    stem = PurePosixPath(relative_path).stem.lower()
    return any(stem.endswith(suffix) for suffix in GENERATED_TABULAR_SUFFIXES)


def _duplicate_group_key(relative_path: str) -> str | None:
    path = PurePosixPath(relative_path)
    extension = path.suffix.lower()

    if extension not in DUPLICATE_FORMAT_EXTENSIONS:
        return None

    stem = path.stem.lower()

    # Restrict automatic duplicate filtering to obvious report-style files.
    if "report" not in stem and "summary" not in stem:
        return None

    return str(path.with_suffix("")).lower()


def apply_index_eligibility(
    files: list[dict[str, Any]],
    *,
    chunk_target_bytes: int = 4000,
) -> dict[str, Any]:
    """Add deterministic RAG index-eligibility metadata to inventory records."""

    if chunk_target_bytes <= 0:
        raise ValueError("chunk_target_bytes must be greater than zero")

    annotated_files: list[dict[str, Any]] = []

    for original in files:
        record = dict(original)
        accepted = bool(record.get("accepted"))
        size_bytes = int(record.get("size_bytes", 0))
        extension = str(record.get("extension", "")).lower()
        relative_path = str(record.get("relative_path", ""))

        if not accepted:
            record["index_eligible"] = False
            record["index_reason"] = "inventory_rejected"
        elif size_bytes <= 0:
            record["index_eligible"] = False
            record["index_reason"] = "skipped_empty_file"
        elif extension in SUSPICIOUS_EXTENSIONS:
            record["index_eligible"] = False
            record["index_reason"] = "skipped_suspicious_extension"
        elif _is_generated_tabular_file(relative_path, extension):
            record["index_eligible"] = False
            record["index_reason"] = "skipped_generated_dataset"
        else:
            record["index_eligible"] = True
            record["index_reason"] = "eligible"

        annotated_files.append(record)

    duplicate_groups: dict[str, list[int]] = defaultdict(list)

    for index, record in enumerate(annotated_files):
        if not record["index_eligible"]:
            continue

        group_key = _duplicate_group_key(record["relative_path"])
        if group_key is not None:
            duplicate_groups[group_key].append(index)

    for indexes in duplicate_groups.values():
        if len(indexes) <= 1:
            continue

        preferred_index = min(
            indexes,
            key=lambda item_index: (
                DUPLICATE_FORMAT_PRIORITY.get(
                    annotated_files[item_index]["extension"],
                    100,
                ),
                annotated_files[item_index]["relative_path"].lower(),
            ),
        )

        for item_index in indexes:
            if item_index == preferred_index:
                continue

            annotated_files[item_index]["index_eligible"] = False
            annotated_files[item_index]["index_reason"] = (
                "skipped_duplicate_format"
            )

    indexable_files = [
        record for record in annotated_files if record["index_eligible"]
    ]

    skipped_accepted_files = [
        record
        for record in annotated_files
        if record.get("accepted") and not record["index_eligible"]
    ]

    indexable_type_counts = Counter(
        record["extension"] for record in indexable_files
    )

    index_skip_reason_counts = Counter(
        record["index_reason"] for record in skipped_accepted_files
    )

    estimated_index_chunk_count = sum(
        max(
            1,
            math.ceil(int(record["size_bytes"]) / chunk_target_bytes),
        )
        for record in indexable_files
        if int(record["size_bytes"]) > 0
    )

    return {
        "files": annotated_files,
        "indexable_files": len(indexable_files),
        "index_skipped_files": len(skipped_accepted_files),
        "indexable_type_counts": dict(indexable_type_counts),
        "index_skip_reason_counts": dict(index_skip_reason_counts),
        "estimated_index_chunk_count": estimated_index_chunk_count,
    }
