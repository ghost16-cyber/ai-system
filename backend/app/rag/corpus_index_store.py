from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.app.rag.corpus_chunker import (
    DEFAULT_MAX_CHARS,
    DEFAULT_OVERLAP_CHARS,
    chunk_text,
)
from backend.app.rag.corpus_inventory import DEFAULT_CORPUS_ROOT, scan_corpus
from backend.app.rag.corpus_text_extractor import extract_corpus_file


DEFAULT_INDEX_ROOT = Path("data/rag/corpus_index")
MANIFEST_FILENAME = "manifest.json"
CHUNKS_FILENAME = "chunks.jsonl"
SCHEMA_VERSION = 1


class MalformedCorpusIndexError(ValueError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_corpus_file(root: Path, relative_path: str) -> Path:
    candidate = (root / relative_path).resolve()

    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise ValueError(
            "Corpus file path escapes the configured corpus root."
        ) from error

    return candidate


def _source_sha256(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)

    return digest.hexdigest()


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")

    try:
        temporary_path.write_text(text, encoding="utf-8")
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _manifest_path(index_root: str | Path) -> Path:
    return Path(index_root) / MANIFEST_FILENAME


def _chunks_path(index_root: str | Path) -> Path:
    return Path(index_root) / CHUNKS_FILENAME


def _load_manifest(index_root: Path) -> dict[str, Any] | None:
    path = _manifest_path(index_root)

    if not path.exists():
        return None

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as error:
        raise MalformedCorpusIndexError(
            f"Unable to read corpus index manifest: {error}"
        ) from error

    if not isinstance(payload, dict):
        raise MalformedCorpusIndexError(
            "Corpus index manifest must contain a JSON object."
        )

    return payload


def _load_chunks(index_root: Path) -> list[dict[str, Any]]:
    path = _chunks_path(index_root)

    if not path.exists():
        return []

    chunks: list[dict[str, Any]] = []

    try:
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(),
            start=1,
        ):
            if not line.strip():
                continue

            item = json.loads(line)

            if not isinstance(item, dict):
                raise MalformedCorpusIndexError(
                    f"Chunk record on line {line_number} is not an object."
                )

            chunks.append(item)
    except json.JSONDecodeError as error:
        raise MalformedCorpusIndexError(
            f"Unable to read corpus index chunks JSONL: {error}"
        ) from error
    except OSError as error:
        raise MalformedCorpusIndexError(
            f"Unable to read corpus index chunks: {error}"
        ) from error

    return chunks


def _load_existing_index(
    index_root: str | Path = DEFAULT_INDEX_ROOT,
) -> tuple[dict[str, Any] | None, dict[str, list[dict[str, Any]]]]:
    root = Path(index_root)
    manifest = _load_manifest(root)
    chunks = _load_chunks(root)

    if manifest is None and not chunks:
        return None, {}

    if manifest is None and chunks:
        raise MalformedCorpusIndexError(
            "Corpus index chunks exist without a manifest."
        )

    files = manifest.get("files", []) if manifest else []

    if not isinstance(files, list):
        raise MalformedCorpusIndexError(
            "Corpus index manifest files must be a list."
        )

    chunk_count = int(manifest.get("chunk_count", 0)) if manifest else 0

    if chunk_count != len(chunks):
        raise MalformedCorpusIndexError(
            "Corpus index manifest chunk count does not match chunks file."
        )

    chunks_by_path: dict[str, list[dict[str, Any]]] = {}
    seen_chunk_ids: set[str] = set()

    for chunk in chunks:
        relative_path = str(chunk.get("relative_path", ""))
        chunk_id = str(chunk.get("chunk_id", ""))

        if not relative_path or not chunk_id:
            raise MalformedCorpusIndexError(
                "Corpus index chunk is missing source metadata."
            )

        if chunk_id in seen_chunk_ids:
            raise MalformedCorpusIndexError(
                "Corpus index chunks contain duplicate chunk IDs."
            )

        seen_chunk_ids.add(chunk_id)
        chunks_by_path.setdefault(relative_path, []).append(chunk)

    return manifest, chunks_by_path


def _write_index(
    *,
    index_root: Path,
    manifest: dict[str, Any],
    chunks: list[dict[str, Any]],
) -> None:
    chunks_text = "".join(
        f"{json.dumps(chunk, sort_keys=True, ensure_ascii=False)}\n"
        for chunk in chunks
    )
    manifest_text = json.dumps(
        manifest,
        indent=2,
        sort_keys=True,
        ensure_ascii=False,
    )

    _atomic_write_text(_chunks_path(index_root), chunks_text)
    _atomic_write_text(_manifest_path(index_root), f"{manifest_text}\n")


def _file_records_from_manifest(
    manifest: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    if not manifest:
        return {}

    records: dict[str, dict[str, Any]] = {}

    for item in manifest.get("files", []):
        if not isinstance(item, dict):
            continue

        relative_path = str(item.get("relative_path", ""))

        if relative_path:
            records[relative_path] = item

    return records


def _can_reuse_chunks(
    *,
    previous_file: dict[str, Any] | None,
    previous_chunks: list[dict[str, Any]],
    source_sha256: str,
    max_chars: int,
    overlap_chars: int,
) -> bool:
    if not previous_file:
        return False

    if previous_file.get("source_sha256") != source_sha256:
        return False

    if int(previous_file.get("max_chars", 0)) != max_chars:
        return False

    if int(previous_file.get("overlap_chars", -1)) != overlap_chars:
        return False

    chunk_ids = previous_file.get("chunk_ids", [])

    if not isinstance(chunk_ids, list):
        return False

    return chunk_ids == [chunk.get("chunk_id") for chunk in previous_chunks]


def _build_file_record(
    *,
    record: dict[str, Any],
    source_sha256: str,
    source_status: str,
    extraction_status: str,
    extraction_reason: str,
    chunks: list[dict[str, Any]],
    max_chars: int,
    overlap_chars: int,
) -> dict[str, Any]:
    return {
        "relative_path": str(record.get("relative_path", "")),
        "extension": str(record.get("extension", "")),
        "size_bytes": int(record.get("size_bytes", 0)),
        "source_sha256": source_sha256,
        "source_status": source_status,
        "index_eligible": bool(record.get("index_eligible", False)),
        "index_reason": str(record.get("index_reason", "")),
        "extraction_status": extraction_status,
        "extraction_reason": extraction_reason,
        "indexed": bool(chunks),
        "chunk_count": len(chunks),
        "chunk_ids": [chunk["chunk_id"] for chunk in chunks],
        "max_chars": max_chars,
        "overlap_chars": overlap_chars,
    }


def build_corpus_index(
    corpus_root: str | Path = DEFAULT_CORPUS_ROOT,
    *,
    index_root: str | Path = DEFAULT_INDEX_ROOT,
    full_rebuild: bool = False,
    max_chars: int = DEFAULT_MAX_CHARS,
    overlap_chars: int = DEFAULT_OVERLAP_CHARS,
) -> dict[str, Any]:
    if max_chars <= 0:
        raise ValueError("max_chars must be greater than zero")

    if overlap_chars < 0:
        raise ValueError("overlap_chars must not be negative")

    if overlap_chars >= max_chars:
        raise ValueError("overlap_chars must be smaller than max_chars")

    corpus_root_path = Path(corpus_root).expanduser().resolve()
    index_root_path = Path(index_root)

    malformed_reason: str | None = None

    try:
        previous_manifest, previous_chunks_by_path = (
            (None, {})
            if full_rebuild
            else _load_existing_index(index_root_path)
        )
    except MalformedCorpusIndexError as error:
        previous_manifest = None
        previous_chunks_by_path = {}
        malformed_reason = str(error)

    previous_files = _file_records_from_manifest(previous_manifest)
    inventory = scan_corpus(corpus_root_path)
    eligible_records = [
        item
        for item in inventory["files"]
        if item.get("index_eligible") is True
    ]

    files: list[dict[str, Any]] = []
    chunks: list[dict[str, Any]] = []
    skipped_files: list[dict[str, Any]] = []
    source_status_counts: Counter[str] = Counter()

    current_paths = {
        str(record.get("relative_path", ""))
        for record in eligible_records
    }

    for record in eligible_records:
        relative_path = str(record.get("relative_path", ""))
        source_status = "added"

        try:
            source_path = _resolve_corpus_file(
                corpus_root_path,
                relative_path,
            )
            source_hash = _source_sha256(source_path)
        except (OSError, ValueError):
            source_hash = ""

        previous_file = previous_files.get(relative_path)
        previous_chunks = previous_chunks_by_path.get(relative_path, [])

        if previous_file and previous_file.get("source_sha256") == source_hash:
            source_status = "unchanged"
        elif previous_file:
            source_status = "changed"

        can_reuse = _can_reuse_chunks(
            previous_file=previous_file,
            previous_chunks=previous_chunks,
            source_sha256=source_hash,
            max_chars=max_chars,
            overlap_chars=overlap_chars,
        )

        if can_reuse:
            file_chunks = [dict(chunk) for chunk in previous_chunks]
            extraction_status = str(
                previous_file.get("extraction_status", "extracted")
            )
            extraction_reason = str(
                previous_file.get("extraction_reason", "ok")
            )
        else:
            extraction = extract_corpus_file(
                corpus_root_path,
                record,
                include_text=True,
            )
            extraction_status = str(extraction["extraction_status"])
            extraction_reason = str(extraction["extraction_reason"])

            if extraction_status == "extracted":
                file_chunks = chunk_text(
                    relative_path=relative_path,
                    extension=str(record.get("extension", "")),
                    text=extraction["text"] or "",
                    max_chars=max_chars,
                    overlap_chars=overlap_chars,
                )

                for chunk in file_chunks:
                    chunk["source_sha256"] = source_hash
            else:
                file_chunks = []
                skipped_files.append(
                    {
                        "relative_path": relative_path,
                        "reason": extraction_reason,
                    }
                )

        source_status_counts[source_status] += 1
        chunks.extend(file_chunks)
        files.append(
            _build_file_record(
                record=record,
                source_sha256=source_hash,
                source_status=source_status,
                extraction_status=extraction_status,
                extraction_reason=extraction_reason,
                chunks=file_chunks,
                max_chars=max_chars,
                overlap_chars=overlap_chars,
            )
        )

    deleted_files = sorted(
        path
        for path in previous_files
        if path not in current_paths
    )

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "mode": "local_persistent_corpus_index",
        "created_at": (
            previous_manifest.get("created_at")
            if previous_manifest and not full_rebuild
            else _utc_now()
        ),
        "updated_at": _utc_now(),
        "corpus_root": str(corpus_root_path),
        "index_root": str(index_root_path),
        "max_chars": max_chars,
        "overlap_chars": overlap_chars,
        "source_file_count": len(files),
        "indexed_file_count": sum(1 for item in files if item["indexed"]),
        "skipped_file_count": len(skipped_files),
        "chunk_count": len(chunks),
        "embeddings_created": False,
        "files": files,
        "skipped_files": skipped_files,
        "last_build": {
            "full_rebuild": full_rebuild,
            "added_file_count": source_status_counts.get("added", 0),
            "changed_file_count": source_status_counts.get("changed", 0),
            "unchanged_file_count": source_status_counts.get(
                "unchanged", 0
            ),
            "deleted_file_count": len(deleted_files),
            "deleted_files": deleted_files,
            "recovered_from_malformed_index": malformed_reason is not None,
            "malformed_index_reason": malformed_reason,
        },
    }

    _write_index(
        index_root=index_root_path,
        manifest=manifest,
        chunks=chunks,
    )

    return {
        "mode": "local_persistent_corpus_index",
        "writes_performed": True,
        "embeddings_created": False,
        "storage_root": str(index_root_path),
        "manifest_path": str(_manifest_path(index_root_path)),
        "chunks_path": str(_chunks_path(index_root_path)),
        "corpus_root": str(corpus_root_path),
        "full_rebuild": full_rebuild,
        "source_file_count": manifest["source_file_count"],
        "indexed_file_count": manifest["indexed_file_count"],
        "skipped_file_count": manifest["skipped_file_count"],
        "chunk_count": manifest["chunk_count"],
        "added_file_count": manifest["last_build"]["added_file_count"],
        "changed_file_count": manifest["last_build"]["changed_file_count"],
        "unchanged_file_count": manifest["last_build"][
            "unchanged_file_count"
        ],
        "deleted_file_count": manifest["last_build"]["deleted_file_count"],
        "deleted_files": deleted_files,
        "recovered_from_malformed_index": malformed_reason is not None,
        "malformed_index_reason": malformed_reason,
    }


def corpus_index_status(
    index_root: str | Path = DEFAULT_INDEX_ROOT,
) -> dict[str, Any]:
    index_root_path = Path(index_root)
    manifest_file = _manifest_path(index_root_path)
    chunks_file = _chunks_path(index_root_path)

    try:
        manifest, _ = _load_existing_index(index_root_path)
    except MalformedCorpusIndexError as error:
        return {
            "mode": "local_persistent_corpus_index",
            "writes_performed": False,
            "embeddings_created": False,
            "storage_root": str(index_root_path),
            "manifest_path": str(manifest_file),
            "chunks_path": str(chunks_file),
            "exists": manifest_file.exists() or chunks_file.exists(),
            "status": "malformed",
            "malformed_reason": str(error),
            "source_file_count": 0,
            "indexed_file_count": 0,
            "chunk_count": 0,
        }

    if manifest is None:
        return {
            "mode": "local_persistent_corpus_index",
            "writes_performed": False,
            "embeddings_created": False,
            "storage_root": str(index_root_path),
            "manifest_path": str(manifest_file),
            "chunks_path": str(chunks_file),
            "exists": False,
            "status": "missing",
            "source_file_count": 0,
            "indexed_file_count": 0,
            "chunk_count": 0,
        }

    return {
        "mode": "local_persistent_corpus_index",
        "writes_performed": False,
        "embeddings_created": False,
        "storage_root": str(index_root_path),
        "manifest_path": str(manifest_file),
        "chunks_path": str(chunks_file),
        "exists": True,
        "status": "ready",
        "schema_version": manifest.get("schema_version"),
        "corpus_root": manifest.get("corpus_root"),
        "updated_at": manifest.get("updated_at"),
        "max_chars": manifest.get("max_chars"),
        "overlap_chars": manifest.get("overlap_chars"),
        "source_file_count": manifest.get("source_file_count", 0),
        "indexed_file_count": manifest.get("indexed_file_count", 0),
        "skipped_file_count": manifest.get("skipped_file_count", 0),
        "chunk_count": manifest.get("chunk_count", 0),
        "last_build": manifest.get("last_build", {}),
    }


def corpus_index_files(
    index_root: str | Path = DEFAULT_INDEX_ROOT,
) -> dict[str, Any]:
    index_root_path = Path(index_root)

    try:
        manifest, _ = _load_existing_index(index_root_path)
    except MalformedCorpusIndexError as error:
        return {
            "mode": "local_persistent_corpus_index",
            "writes_performed": False,
            "embeddings_created": False,
            "storage_root": str(index_root_path),
            "status": "malformed",
            "malformed_reason": str(error),
            "files": [],
        }

    if manifest is None:
        return {
            "mode": "local_persistent_corpus_index",
            "writes_performed": False,
            "embeddings_created": False,
            "storage_root": str(index_root_path),
            "status": "missing",
            "files": [],
        }

    files = sorted(
        manifest.get("files", []),
        key=lambda item: str(item.get("relative_path", "")),
    )

    return {
        "mode": "local_persistent_corpus_index",
        "writes_performed": False,
        "embeddings_created": False,
        "storage_root": str(index_root_path),
        "status": "ready",
        "source_file_count": manifest.get("source_file_count", 0),
        "indexed_file_count": manifest.get("indexed_file_count", 0),
        "skipped_file_count": manifest.get("skipped_file_count", 0),
        "chunk_count": manifest.get("chunk_count", 0),
        "files": files,
    }
