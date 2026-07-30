from __future__ import annotations

import math
import os
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from backend.app.rag.corpus_eligibility import apply_index_eligibility


DEFAULT_CORPUS_ROOT = Path("astra_corpus")

ACCEPTED_EXTENSIONS = {
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
    ".docx",
    ".pdf",
    ".cvs",
    ".csv1",
    ".example",
}

IGNORED_EXTENSIONS = {
    ".crc",
    ".parquet",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".pt",
    ".pth",
    ".db",
    ".sqlite",
    ".lnk",
}

IGNORED_FILE_NAMES = {
    ".env",
    "env",
}

IGNORED_DIR_NAMES = {
    "venv",
    "env",
    "node_modules",
    "__pycache__",
    ".git",
    ".pytest_cache",
    ".mypy_cache",
    "site-packages",
}

CHUNK_TARGET_BYTES = 4000
MAX_ACCEPTED_FILE_BYTES = 2_000_000

SIZE_LIMITED_EXTENSIONS = {
    ".csv",
    ".txt",
    ".json",
    ".html",
    ".css",
    ".sql",
    ".yml",
    ".yaml",
    ".md",
    ".example",
    ".cvs",
    ".csv1",
}


@dataclass(frozen=True)
class CorpusFileRecord:
    relative_path: str
    extension: str
    size_bytes: int
    accepted: bool
    reason: str


def _normalise_relative_path(path: Path, root: Path) -> str:
    return str(path.relative_to(root)).replace("\\", "/")


def _display_extension(path: Path) -> str:
    suffix = path.suffix.lower()
    return suffix if suffix else "[no extension]"


def _should_prune_dir(dirname: str) -> bool:
    lowered = dirname.lower()

    if lowered in IGNORED_DIR_NAMES:
        return True

    if lowered.startswith(".venv"):
        return True

    return False


def _has_ignored_path_part(path: Path) -> bool:
    return any(part.lower() in IGNORED_DIR_NAMES for part in path.parts)


def _classify_file(path: Path, root: Path) -> CorpusFileRecord:
    relative_path = _normalise_relative_path(path, root)
    extension = _display_extension(path)
    suffix = path.suffix.lower()
    filename = path.name.lower()

    try:
        size_bytes = path.stat().st_size
    except OSError:
        return CorpusFileRecord(
            relative_path=relative_path,
            extension=extension,
            size_bytes=0,
            accepted=False,
            reason="ignored_unreadable_file",
        )

    if _has_ignored_path_part(path.relative_to(root)):
        return CorpusFileRecord(
            relative_path=relative_path,
            extension=extension,
            size_bytes=size_bytes,
            accepted=False,
            reason="ignored_folder",
        )

    if filename in IGNORED_FILE_NAMES:
        return CorpusFileRecord(
            relative_path=relative_path,
            extension=extension,
            size_bytes=size_bytes,
            accepted=False,
            reason="ignored_sensitive_env_file",
        )

    if suffix in IGNORED_EXTENSIONS:
        return CorpusFileRecord(
            relative_path=relative_path,
            extension=extension,
            size_bytes=size_bytes,
            accepted=False,
            reason="ignored_extension",
        )

    if suffix in SIZE_LIMITED_EXTENSIONS and size_bytes > MAX_ACCEPTED_FILE_BYTES:
        return CorpusFileRecord(
            relative_path=relative_path,
            extension=extension,
            size_bytes=size_bytes,
            accepted=False,
            reason="ignored_oversized_file",
        )

    if suffix in ACCEPTED_EXTENSIONS:
        return CorpusFileRecord(
            relative_path=relative_path,
            extension=extension,
            size_bytes=size_bytes,
            accepted=True,
            reason="accepted_extension",
        )

    if not suffix:
        return CorpusFileRecord(
            relative_path=relative_path,
            extension=extension,
            size_bytes=size_bytes,
            accepted=False,
            reason="ignored_no_extension",
        )

    return CorpusFileRecord(
        relative_path=relative_path,
        extension=extension,
        size_bytes=size_bytes,
        accepted=False,
        reason="ignored_unknown_extension",
    )


def _estimate_chunks(records: list[CorpusFileRecord]) -> int:
    total = 0

    for record in records:
        if not record.accepted:
            continue

        if record.size_bytes <= 0:
            continue

        total += max(1, math.ceil(record.size_bytes / CHUNK_TARGET_BYTES))

    return total


def scan_corpus(root: str | Path = DEFAULT_CORPUS_ROOT) -> dict[str, Any]:
    root_path = Path(root)

    if not root_path.exists():
        return {
            "root": str(root_path),
            "corpus_exists": False,
            "total_files": 0,
            "accepted_files": 0,
            "ignored_files": 0,
            "file_type_counts": {},
            "accepted_type_counts": {},
            "ignored_type_counts": {},
            "largest_files": [],
            "estimated_chunk_count": 0,
            "estimated_index_chunk_count": 0,
            "indexable_files": 0,
            "index_skipped_files": 0,
            "indexable_type_counts": {},
            "index_skip_reason_counts": {},
            "files": [],
        }

    records: list[CorpusFileRecord] = []

    for current_root, dirnames, filenames in os.walk(root_path):
        dirnames[:] = sorted(
            dirname for dirname in dirnames if not _should_prune_dir(dirname)
        )

        for filename in sorted(filenames):
            path = Path(current_root) / filename
            records.append(_classify_file(path, root_path))

    accepted_records = [record for record in records if record.accepted]
    ignored_records = [record for record in records if not record.accepted]

    file_type_counts = Counter(record.extension for record in records)
    accepted_type_counts = Counter(record.extension for record in accepted_records)
    ignored_type_counts = Counter(record.extension for record in ignored_records)

    eligibility = apply_index_eligibility(
        [asdict(record) for record in records],
        chunk_target_bytes=CHUNK_TARGET_BYTES,
    )

    largest_files = sorted(
        records,
        key=lambda record: record.size_bytes,
        reverse=True,
    )[:10]

    return {
        "root": str(root_path),
        "corpus_exists": True,
        "total_files": len(records),
        "accepted_files": len(accepted_records),
        "ignored_files": len(ignored_records),
        "file_type_counts": dict(file_type_counts),
        "accepted_type_counts": dict(accepted_type_counts),
        "ignored_type_counts": dict(ignored_type_counts),
        "largest_files": [asdict(record) for record in largest_files],
        "estimated_chunk_count": _estimate_chunks(records),
        "estimated_index_chunk_count": eligibility[
            "estimated_index_chunk_count"
        ],
        "indexable_files": eligibility["indexable_files"],
        "index_skipped_files": eligibility["index_skipped_files"],
        "indexable_type_counts": eligibility["indexable_type_counts"],
        "index_skip_reason_counts": eligibility[
            "index_skip_reason_counts"
        ],
        "files": eligibility["files"],
    }
