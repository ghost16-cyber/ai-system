from __future__ import annotations

import hashlib
from collections import Counter
from pathlib import Path
from typing import Any

from backend.app.rag.corpus_inventory import DEFAULT_CORPUS_ROOT
from backend.app.rag.corpus_text_extractor import extract_indexable_corpus


DEFAULT_MAX_CHARS = 4000
DEFAULT_OVERLAP_CHARS = 400


def _stable_chunk_id(
    relative_path: str,
    chunk_index: int,
    text: str,
) -> str:
    payload = f"{relative_path}\n{chunk_index}\n{text}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _line_offsets(text: str) -> list[int]:
    offsets = [0]

    for index, character in enumerate(text):
        if character == "\n":
            offsets.append(index + 1)

    return offsets


def _line_number_for_offset(
    offsets: list[int],
    character_offset: int,
) -> int:
    line_number = 1

    for index, offset in enumerate(offsets, start=1):
        if offset > character_offset:
            break
        line_number = index

    return line_number


def _choose_chunk_end(
    text: str,
    start: int,
    max_chars: int,
) -> int:
    proposed_end = min(len(text), start + max_chars)

    if proposed_end >= len(text):
        return len(text)

    newline_position = text.rfind(
        "\n",
        start,
        proposed_end,
    )

    if newline_position > start:
        return newline_position + 1

    return proposed_end


def chunk_text(
    *,
    relative_path: str,
    extension: str,
    text: str,
    max_chars: int = DEFAULT_MAX_CHARS,
    overlap_chars: int = DEFAULT_OVERLAP_CHARS,
) -> list[dict[str, Any]]:
    if max_chars <= 0:
        raise ValueError("max_chars must be greater than zero")

    if overlap_chars < 0:
        raise ValueError("overlap_chars must not be negative")

    if overlap_chars >= max_chars:
        raise ValueError(
            "overlap_chars must be smaller than max_chars"
        )

    if not text:
        return []

    offsets = _line_offsets(text)
    chunks: list[dict[str, Any]] = []
    start = 0
    chunk_index = 0

    while start < len(text):
        end = _choose_chunk_end(
            text,
            start,
            max_chars,
        )

        if end <= start:
            end = min(len(text), start + max_chars)

        chunk_text_value = text[start:end]
        start_line = _line_number_for_offset(offsets, start)
        end_line = _line_number_for_offset(
            offsets,
            max(start, end - 1),
        )

        chunks.append(
            {
                "chunk_id": _stable_chunk_id(
                    relative_path,
                    chunk_index,
                    chunk_text_value,
                ),
                "relative_path": relative_path,
                "extension": extension,
                "chunk_index": chunk_index,
                "start_line": start_line,
                "end_line": end_line,
                "character_count": len(chunk_text_value),
                "text": chunk_text_value,
            }
        )

        if end >= len(text):
            break

        start = max(
            start + 1,
            end - overlap_chars,
        )
        chunk_index += 1

    return chunks


def build_corpus_chunk_preview(
    root: str | Path = DEFAULT_CORPUS_ROOT,
    *,
    file_limit: int = 100,
    include_text: bool = False,
    max_chars: int = DEFAULT_MAX_CHARS,
    overlap_chars: int = DEFAULT_OVERLAP_CHARS,
) -> dict[str, Any]:
    extraction = extract_indexable_corpus(
        root,
        include_text=True,
        limit=file_limit,
    )

    chunks: list[dict[str, Any]] = []
    skipped_files: list[dict[str, Any]] = []

    for file_record in extraction["files"]:
        if file_record["extraction_status"] != "extracted":
            skipped_files.append(
                {
                    "relative_path": file_record["relative_path"],
                    "reason": file_record["extraction_reason"],
                }
            )
            continue

        file_chunks = chunk_text(
            relative_path=file_record["relative_path"],
            extension=file_record["extension"],
            text=file_record["text"] or "",
            max_chars=max_chars,
            overlap_chars=overlap_chars,
        )

        if not include_text:
            for chunk in file_chunks:
                chunk["text"] = None

        chunks.extend(file_chunks)

    chunks_per_file = Counter(
        chunk["relative_path"] for chunk in chunks
    )

    return {
        "root": extraction["root"],
        "mode": "read_only_chunk_preview",
        "writes_performed": False,
        "embeddings_created": False,
        "eligible_file_count": extraction[
            "eligible_file_count"
        ],
        "processed_file_count": extraction[
            "processed_file_count"
        ],
        "chunked_file_count": len(chunks_per_file),
        "skipped_file_count": len(skipped_files),
        "chunk_count": len(chunks),
        "max_chars": max_chars,
        "overlap_chars": overlap_chars,
        "chunks_per_file": dict(chunks_per_file),
        "chunks": chunks,
        "skipped_files": skipped_files,
    }
