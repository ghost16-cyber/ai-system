from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from backend.app.rag.corpus_inventory import (
    DEFAULT_CORPUS_ROOT,
    scan_corpus,
)


SUPPORTED_TEXT_EXTENSIONS = {
    ".py",
    ".txt",
    ".md",
    ".json",
    ".csv",
    ".html",
    ".css",
    ".sql",
    ".yml",
    ".yaml",
    ".example",
}

MAX_EXTRACTED_CHARACTERS = 1_000_000


def _normalise_text(text: str) -> str:
    """Normalise safe text without interpreting or executing it."""
    return (
        text.replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\x00", "")
    )


def _resolve_corpus_file(
    root: Path,
    relative_path: str,
) -> Path:
    candidate = (root / relative_path).resolve()

    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise ValueError(
            "Corpus file path escapes the configured corpus root."
        ) from error

    return candidate


def extract_corpus_file(
    root: str | Path,
    record: dict[str, Any],
    *,
    include_text: bool = True,
    max_characters: int = MAX_EXTRACTED_CHARACTERS,
) -> dict[str, Any]:
    if max_characters <= 0:
        raise ValueError("max_characters must be greater than zero")

    root_path = Path(root).expanduser().resolve()
    relative_path = str(record.get("relative_path", ""))
    extension = str(record.get("extension", "")).lower()
    index_eligible = bool(record.get("index_eligible", False))

    result: dict[str, Any] = {
        "relative_path": relative_path,
        "extension": extension,
        "size_bytes": int(record.get("size_bytes", 0)),
        "index_eligible": index_eligible,
        "extraction_status": "skipped",
        "extraction_reason": "not_index_eligible",
        "character_count": 0,
        "line_count": 0,
        "truncated": False,
        "text": None,
    }

    if not index_eligible:
        return result

    if extension not in SUPPORTED_TEXT_EXTENSIONS:
        result["extraction_reason"] = "unsupported_binary_format"
        return result

    try:
        resolved_path = _resolve_corpus_file(
            root_path,
            relative_path,
        )
    except ValueError:
        result["extraction_status"] = "failed"
        result["extraction_reason"] = "path_outside_corpus_root"
        return result

    if not resolved_path.exists():
        result["extraction_status"] = "failed"
        result["extraction_reason"] = "file_not_found"
        return result

    if not resolved_path.is_file():
        result["extraction_status"] = "failed"
        result["extraction_reason"] = "not_a_file"
        return result

    try:
        raw_text = resolved_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        result["extraction_status"] = "failed"
        result["extraction_reason"] = "invalid_utf8"
        return result
    except OSError:
        result["extraction_status"] = "failed"
        result["extraction_reason"] = "read_error"
        return result

    normalised = _normalise_text(raw_text)

    if len(normalised) > max_characters:
        normalised = normalised[:max_characters]
        result["truncated"] = True

    result["extraction_status"] = "extracted"
    result["extraction_reason"] = "ok"
    result["character_count"] = len(normalised)
    result["line_count"] = len(normalised.splitlines())

    if include_text:
        result["text"] = normalised

    return result


def extract_indexable_corpus(
    root: str | Path = DEFAULT_CORPUS_ROOT,
    *,
    include_text: bool = False,
    limit: int = 100,
    max_characters: int = MAX_EXTRACTED_CHARACTERS,
) -> dict[str, Any]:
    if limit < 0:
        raise ValueError("limit must not be negative")

    inventory = scan_corpus(root)
    root_path = Path(root).expanduser().resolve()

    eligible_records = [
        item
        for item in inventory["files"]
        if item.get("index_eligible") is True
    ]

    selected_records = eligible_records[:limit]
    extracted_files = [
        extract_corpus_file(
            root_path,
            record,
            include_text=include_text,
            max_characters=max_characters,
        )
        for record in selected_records
    ]

    status_counts = Counter(
        item["extraction_status"] for item in extracted_files
    )
    reason_counts = Counter(
        item["extraction_reason"] for item in extracted_files
    )

    return {
        "root": inventory["root"],
        "corpus_exists": inventory["corpus_exists"],
        "mode": "read_only_extraction",
        "writes_performed": False,
        "embeddings_created": False,
        "chunking_performed": False,
        "eligible_file_count": len(eligible_records),
        "processed_file_count": len(extracted_files),
        "remaining_file_count": max(
            0,
            len(eligible_records) - len(extracted_files),
        ),
        "extracted_file_count": status_counts.get("extracted", 0),
        "skipped_file_count": status_counts.get("skipped", 0),
        "failed_file_count": status_counts.get("failed", 0),
        "status_counts": dict(status_counts),
        "reason_counts": dict(reason_counts),
        "files": extracted_files,
    }
