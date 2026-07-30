from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.app.rag.corpus_index_store import DEFAULT_INDEX_ROOT
from backend.app.rag.embedding_provider import EmbeddingProvider


DEFAULT_VECTOR_ROOT = Path("data/rag/corpus_vectors")
MANIFEST_FILENAME = "manifest.json"
VECTORS_FILENAME = "vectors.jsonl"
VECTOR_STORE_SCHEMA_VERSION = 1


class CorpusVectorStoreError(ValueError):
    pass


class MalformedVectorStoreError(CorpusVectorStoreError):
    pass


class IncompatibleEmbeddingConfigurationError(CorpusVectorStoreError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _manifest_path(root: Path) -> Path:
    return root / MANIFEST_FILENAME


def _vectors_path(root: Path) -> Path:
    return root / VECTORS_FILENAME


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as error:
        raise MalformedVectorStoreError(f"Unable to read {label}: {error}") from error
    if not isinstance(payload, dict):
        raise MalformedVectorStoreError(f"{label} must contain a JSON object.")
    return payload


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        for line_number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            item = json.loads(line)
            if not isinstance(item, dict):
                raise MalformedVectorStoreError(
                    f"Vector record on line {line_number} is not an object."
                )
            records.append(item)
    except json.JSONDecodeError as error:
        raise MalformedVectorStoreError(
            f"Unable to read corpus vectors JSONL: {error}"
        ) from error
    except OSError as error:
        raise MalformedVectorStoreError(f"Unable to read corpus vectors: {error}") from error
    return records


def _load_chunk_index(index_root: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    manifest_path = index_root / "manifest.json"
    chunks_path = index_root / "chunks.jsonl"
    if not manifest_path.exists():
        raise CorpusVectorStoreError("Corpus chunk index is missing; build it before embeddings.")
    if not chunks_path.exists():
        raise CorpusVectorStoreError("Corpus chunk index chunks file is missing.")

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        chunks = [
            json.loads(line)
            for line in chunks_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (json.JSONDecodeError, OSError) as error:
        raise CorpusVectorStoreError(f"Corpus chunk index is malformed: {error}") from error

    if not isinstance(manifest, dict) or any(not isinstance(item, dict) for item in chunks):
        raise CorpusVectorStoreError("Corpus chunk index has invalid record types.")
    try:
        chunk_count = int(manifest.get("chunk_count", -1))
    except (TypeError, ValueError) as error:
        raise CorpusVectorStoreError("Corpus chunk index count is invalid.") from error
    if chunk_count != len(chunks):
        raise CorpusVectorStoreError("Corpus chunk index count does not match chunks.jsonl.")

    seen: set[str] = set()
    for chunk in chunks:
        chunk_id = str(chunk.get("chunk_id", ""))
        if not chunk_id or chunk_id in seen or not isinstance(chunk.get("text"), str):
            raise CorpusVectorStoreError("Corpus chunk index contains invalid or duplicate chunks.")
        seen.add(chunk_id)
    return manifest, chunks


def _load_vector_store(root: Path) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    manifest_path = _manifest_path(root)
    vectors_path = _vectors_path(root)
    if not manifest_path.exists() and not vectors_path.exists():
        return None, []
    if not manifest_path.exists() or not vectors_path.exists():
        raise MalformedVectorStoreError("Vector manifest and vectors file must both exist.")

    manifest = _read_json(manifest_path, "corpus vector manifest")
    records = _read_jsonl(vectors_path)
    if manifest.get("schema_version") != VECTOR_STORE_SCHEMA_VERSION:
        raise MalformedVectorStoreError("Unsupported corpus vector schema version.")
    if not isinstance(manifest.get("embedding"), dict):
        raise MalformedVectorStoreError("Vector manifest embedding configuration is missing.")
    try:
        vector_count = int(manifest.get("vector_count", -1))
        dimension = int(manifest["embedding"].get("dimension", 0))
    except (TypeError, ValueError) as error:
        raise MalformedVectorStoreError(
            "Vector manifest count or dimension is invalid."
        ) from error
    if vector_count != len(records):
        raise MalformedVectorStoreError("Vector manifest count does not match vectors.jsonl.")
    if dimension <= 0:
        raise MalformedVectorStoreError("Vector manifest dimension must be positive.")
    seen: set[str] = set()
    for record in records:
        chunk_id = str(record.get("chunk_id", ""))
        vector = record.get("vector")
        if not chunk_id or chunk_id in seen:
            raise MalformedVectorStoreError("Vector store contains missing or duplicate chunk IDs.")
        if not isinstance(vector, list) or len(vector) != dimension:
            raise MalformedVectorStoreError("Vector record has an invalid dimension.")
        if any(not isinstance(value, (int, float)) for value in vector):
            raise MalformedVectorStoreError("Vector record contains non-numeric values.")
        for field in ("source_path", "source_hash", "chunk_hash", "text"):
            if not isinstance(record.get(field), str):
                raise MalformedVectorStoreError(f"Vector record is missing {field}.")
        if not isinstance(record.get("chunk_index"), int):
            raise MalformedVectorStoreError("Vector record has an invalid chunk index.")
        seen.add(chunk_id)
    return manifest, records


def _provider_configuration(provider: EmbeddingProvider) -> dict[str, Any]:
    configuration = dict(provider.configuration())
    required = {
        "provider": provider.provider_name,
        "model": provider.model_name,
        "dimension": provider.dimension,
        "normalization": provider.normalization,
    }
    configuration.update(required)
    return configuration


def _compatibility_key(configuration: dict[str, Any]) -> dict[str, Any]:
    return {
        key: configuration.get(key)
        for key in ("provider", "model", "dimension", "normalization")
    }


def _chunk_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _identity(item: dict[str, Any]) -> tuple[str, int]:
    source_path = str(item.get("source_path", item.get("relative_path", "")))
    return source_path, int(item.get("chunk_index", -1))


def _record_from_chunk(
    chunk: dict[str, Any],
    vector: list[float],
    configuration: dict[str, Any],
) -> dict[str, Any]:
    text = str(chunk["text"])
    return {
        "chunk_id": str(chunk["chunk_id"]),
        "source_path": str(chunk.get("relative_path", "")),
        "source_hash": str(chunk.get("source_sha256", "")),
        "chunk_index": int(chunk.get("chunk_index", 0)),
        "chunk_hash": _chunk_hash(text),
        "text": text,
        "start_line": chunk.get("start_line"),
        "end_line": chunk.get("end_line"),
        "extension": chunk.get("extension"),
        "vector": [float(value) for value in vector],
        "embedding_provider": configuration["provider"],
        "embedding_model": configuration["model"],
        "embedding_dimension": configuration["dimension"],
    }


def build_corpus_vectors(
    provider: EmbeddingProvider,
    *,
    index_root: str | Path = DEFAULT_INDEX_ROOT,
    vector_root: str | Path = DEFAULT_VECTOR_ROOT,
    full_rebuild: bool = False,
) -> dict[str, Any]:
    index_root_path = Path(index_root)
    vector_root_path = Path(vector_root)
    index_manifest, chunks = _load_chunk_index(index_root_path)
    configuration = _provider_configuration(provider)
    malformed_reason: str | None = None

    try:
        previous_manifest, previous_records = (
            (None, []) if full_rebuild else _load_vector_store(vector_root_path)
        )
    except MalformedVectorStoreError as error:
        previous_manifest, previous_records = None, []
        malformed_reason = str(error)

    if previous_manifest and _compatibility_key(previous_manifest["embedding"]) != _compatibility_key(configuration):
        raise IncompatibleEmbeddingConfigurationError(
            "Embedding configuration changed; rerun with full_rebuild=true."
        )

    previous_by_identity = {_identity(item): item for item in previous_records}
    current_identities = {_identity(item) for item in chunks}
    embedded_new: list[str] = []
    embedded_changed: list[str] = []
    reused_unchanged: list[str] = []
    pending_chunks: list[dict[str, Any]] = []
    vectors_by_chunk_id: dict[str, list[float]] = {}

    for chunk in chunks:
        previous = previous_by_identity.get(_identity(chunk))
        text_hash = _chunk_hash(str(chunk["text"]))
        if previous and previous.get("chunk_hash") == text_hash and not full_rebuild:
            reused_unchanged.append(str(chunk["chunk_id"]))
            vectors_by_chunk_id[str(chunk["chunk_id"])] = [float(value) for value in previous["vector"]]
        else:
            pending_chunks.append(chunk)
            target = embedded_changed if previous and not full_rebuild else embedded_new
            target.append(str(chunk["chunk_id"]))

    if pending_chunks:
        embedded_vectors = provider.embed_texts([str(item["text"]) for item in pending_chunks])
        if len(embedded_vectors) != len(pending_chunks):
            raise CorpusVectorStoreError("Embedding provider returned the wrong number of vectors.")
        for chunk, vector in zip(pending_chunks, embedded_vectors, strict=True):
            if len(vector) != provider.dimension:
                raise CorpusVectorStoreError("Embedding provider returned an incompatible dimension.")
            vectors_by_chunk_id[str(chunk["chunk_id"])] = vector

    records = [
        _record_from_chunk(chunk, vectors_by_chunk_id[str(chunk["chunk_id"])], configuration)
        for chunk in chunks
    ]
    deleted = [
        str(item["chunk_id"])
        for item in previous_records
        if _identity(item) not in current_identities
    ]
    now = _utc_now()
    manifest = {
        "schema_version": VECTOR_STORE_SCHEMA_VERSION,
        "mode": "local_persistent_corpus_vectors",
        "created_at": previous_manifest.get("created_at") if previous_manifest and not full_rebuild else now,
        "updated_at": now,
        "index_root": str(index_root_path),
        "vector_root": str(vector_root_path),
        "source_index_schema_version": index_manifest.get("schema_version"),
        "source_index_updated_at": index_manifest.get("updated_at"),
        "embedding": configuration,
        "vector_count": len(records),
        "last_build": {
            "full_rebuild": full_rebuild,
            "embedded_new": len(embedded_new),
            "embedded_changed": len(embedded_changed),
            "reused_unchanged": len(reused_unchanged),
            "deleted": len(deleted),
            "recovered_from_malformed_store": malformed_reason is not None,
            "malformed_store_reason": malformed_reason,
        },
    }
    vectors_text = "".join(
        f"{json.dumps(item, sort_keys=True, ensure_ascii=False)}\n" for item in records
    )
    _atomic_write_text(_vectors_path(vector_root_path), vectors_text)
    _atomic_write_text(
        _manifest_path(vector_root_path),
        f"{json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False)}\n",
    )
    return {
        "status": "ready",
        "writes_performed": True,
        "full_rebuild": full_rebuild,
        "storage_root": str(vector_root_path),
        "manifest_path": str(_manifest_path(vector_root_path)),
        "vectors_path": str(_vectors_path(vector_root_path)),
        "embedding": configuration,
        "embedded_new": len(embedded_new),
        "embedded_changed": len(embedded_changed),
        "reused_unchanged": len(reused_unchanged),
        "deleted": len(deleted),
        "total_vectors": len(records),
        "recovered_from_malformed_store": malformed_reason is not None,
        "malformed_store_reason": malformed_reason,
    }


def corpus_vector_status(
    vector_root: str | Path = DEFAULT_VECTOR_ROOT,
    *,
    provider: EmbeddingProvider | None = None,
) -> dict[str, Any]:
    root = Path(vector_root)
    try:
        manifest, records = _load_vector_store(root)
    except MalformedVectorStoreError as error:
        return {
            "status": "malformed",
            "exists": _manifest_path(root).exists() or _vectors_path(root).exists(),
            "storage_root": str(root),
            "vector_count": 0,
            "malformed_reason": str(error),
        }
    if manifest is None:
        return {"status": "missing", "exists": False, "storage_root": str(root), "vector_count": 0}
    compatible = True
    if provider is not None:
        compatible = _compatibility_key(manifest["embedding"]) == _compatibility_key(_provider_configuration(provider))
    return {
        "status": "ready" if compatible else "incompatible",
        "exists": True,
        "storage_root": str(root),
        "manifest_path": str(_manifest_path(root)),
        "vectors_path": str(_vectors_path(root)),
        "schema_version": manifest["schema_version"],
        "embedding": manifest["embedding"],
        "vector_count": len(records),
        "updated_at": manifest.get("updated_at"),
        "last_build": manifest.get("last_build", {}),
    }


def corpus_vector_files(
    vector_root: str | Path = DEFAULT_VECTOR_ROOT,
    *,
    provider: EmbeddingProvider | None = None,
) -> dict[str, Any]:
    status = corpus_vector_status(vector_root, provider=provider)
    if status["status"] != "ready":
        return {**status, "files": []}
    _, records = _load_vector_store(Path(vector_root))
    files: dict[str, dict[str, Any]] = {}
    for record in records:
        source_path = record["source_path"]
        item = files.setdefault(source_path, {"source_path": source_path, "source_hash": record["source_hash"], "vector_count": 0})
        item["vector_count"] += 1
    return {**status, "files": sorted(files.values(), key=lambda item: item["source_path"])}


def load_corpus_vectors(
    vector_root: str | Path,
    provider: EmbeddingProvider,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    manifest, records = _load_vector_store(Path(vector_root))
    if manifest is None:
        raise CorpusVectorStoreError("Corpus vector store is missing; build embeddings first.")
    if _compatibility_key(manifest["embedding"]) != _compatibility_key(_provider_configuration(provider)):
        raise IncompatibleEmbeddingConfigurationError("Configured embedding provider is incompatible with the vector store.")
    return manifest, records
