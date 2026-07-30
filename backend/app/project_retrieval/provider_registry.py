from __future__ import annotations

import os
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import Field

from backend.app.project_control.contracts import StrictModel, content_hash


MODEL_POLICY_VERSION = "astra.rag.learned-model-policy.v1"
BGE_TRANSFORM_POLICY_VERSION = "astra.rag.bge-transform.v1"
MINILM_TRANSFORM_POLICY_VERSION = "astra.rag.minilm-transform.v1"
CROSS_ENCODER_POLICY_VERSION = "astra.rag.cross-encoder.v1"


class ProviderDevice(StrEnum):
    CPU = "cpu"
    CUDA = "cuda"
    AUTO = "auto"


class RetrievalModelSpec(StrictModel):
    provider_type: Literal["sentence_transformer", "cross_encoder"]
    model_id: str
    configured_revision: str
    dimensions: int | None = Field(default=None, ge=1, le=4096)
    maximum_sequence_length: int = Field(ge=1, le=8192)
    normalize_embeddings: bool
    query_prefix: str
    passage_prefix: str
    transform_policy_version: str
    trust_level: Literal["local_cached_model"] = "local_cached_model"
    supported_devices: tuple[Literal["cpu", "cuda"], ...] = ("cpu", "cuda")
    default_batch_size: int = Field(ge=1, le=128)
    maximum_batch_size: int = Field(ge=1, le=128)
    estimated_ram_bytes: int = Field(ge=0)
    estimated_vram_bytes: int = Field(ge=0)
    model_policy_version: Literal["astra.rag.learned-model-policy.v1"] = (
        MODEL_POLICY_VERSION
    )


EMBEDDING_MODELS: dict[str, RetrievalModelSpec] = {
    "BAAI/bge-small-en-v1.5": RetrievalModelSpec(
        provider_type="sentence_transformer",
        model_id="BAAI/bge-small-en-v1.5",
        configured_revision="main",
        dimensions=384,
        maximum_sequence_length=512,
        normalize_embeddings=True,
        query_prefix="Represent this sentence for searching relevant passages: ",
        passage_prefix="",
        transform_policy_version=BGE_TRANSFORM_POLICY_VERSION,
        default_batch_size=16,
        maximum_batch_size=64,
        estimated_ram_bytes=512 * 1024**2,
        estimated_vram_bytes=256 * 1024**2,
    ),
    "sentence-transformers/all-MiniLM-L6-v2": RetrievalModelSpec(
        provider_type="sentence_transformer",
        model_id="sentence-transformers/all-MiniLM-L6-v2",
        configured_revision="main",
        dimensions=384,
        maximum_sequence_length=256,
        normalize_embeddings=True,
        query_prefix="",
        passage_prefix="",
        transform_policy_version=MINILM_TRANSFORM_POLICY_VERSION,
        default_batch_size=16,
        maximum_batch_size=64,
        estimated_ram_bytes=384 * 1024**2,
        estimated_vram_bytes=160 * 1024**2,
    ),
}

RERANKER_MODELS: dict[str, RetrievalModelSpec] = {
    "cross-encoder/ms-marco-MiniLM-L6-v2": RetrievalModelSpec(
        provider_type="cross_encoder",
        model_id="cross-encoder/ms-marco-MiniLM-L6-v2",
        configured_revision="main",
        maximum_sequence_length=512,
        normalize_embeddings=False,
        query_prefix="",
        passage_prefix="",
        transform_policy_version=CROSS_ENCODER_POLICY_VERSION,
        default_batch_size=8,
        maximum_batch_size=20,
        estimated_ram_bytes=512 * 1024**2,
        estimated_vram_bytes=256 * 1024**2,
    ),
}


class LocalModelResolution(StrictModel):
    model_id: str
    configured_revision: str
    locally_cached: bool
    resolved_revision: str
    snapshot_path: str | None = None
    effective_identity: str = Field(min_length=64, max_length=64)


def embedding_spec(model_id: str) -> RetrievalModelSpec:
    try:
        return EMBEDDING_MODELS[model_id]
    except KeyError as exc:
        raise ValueError("rag_embedding_model_not_allowlisted") from exc


def reranker_spec(model_id: str) -> RetrievalModelSpec:
    try:
        return RERANKER_MODELS[model_id]
    except KeyError as exc:
        raise ValueError("rag_reranker_model_not_allowlisted") from exc


def resolve_local_model(
    spec: RetrievalModelSpec,
    *,
    cache_root: Path | None = None,
) -> LocalModelResolution:
    root = cache_root or _huggingface_hub_root()
    model_root = root / f"models--{spec.model_id.replace('/', '--')}"
    snapshots = model_root / "snapshots"
    revision: str | None = None
    reference = model_root / "refs" / spec.configured_revision
    if reference.is_file():
        try:
            candidate = reference.read_text(encoding="utf-8").strip()
        except OSError:
            candidate = ""
        if candidate and (snapshots / candidate).is_dir():
            revision = candidate
    if revision is None and snapshots.is_dir():
        available = sorted(
            item.name for item in snapshots.iterdir() if item.is_dir()
        )
        if len(available) == 1:
            revision = available[0]
    locally_cached = revision is not None
    resolved = revision or "unresolved-local-cache"
    snapshot_path = (snapshots / revision).resolve().as_posix() if revision else None
    identity = content_hash({
        "provider_type": spec.provider_type,
        "model_id": spec.model_id,
        "configured_revision": spec.configured_revision,
        "resolved_revision": resolved,
        "model_policy_version": spec.model_policy_version,
        "transform_policy_version": spec.transform_policy_version,
        "dimensions": spec.dimensions,
        "normalization": spec.normalize_embeddings,
    })
    return LocalModelResolution(
        model_id=spec.model_id,
        configured_revision=spec.configured_revision,
        locally_cached=locally_cached,
        resolved_revision=resolved,
        snapshot_path=snapshot_path,
        effective_identity=identity,
    )


def _huggingface_hub_root() -> Path:
    if value := os.getenv("HUGGINGFACE_HUB_CACHE"):
        return Path(value).expanduser()
    if value := os.getenv("HF_HOME"):
        return Path(value).expanduser() / "hub"
    return Path.home() / ".cache" / "huggingface" / "hub"


__all__ = [
    "BGE_TRANSFORM_POLICY_VERSION",
    "CROSS_ENCODER_POLICY_VERSION",
    "EMBEDDING_MODELS",
    "LocalModelResolution",
    "MINILM_TRANSFORM_POLICY_VERSION",
    "MODEL_POLICY_VERSION",
    "ProviderDevice",
    "RERANKER_MODELS",
    "RetrievalModelSpec",
    "embedding_spec",
    "reranker_spec",
    "resolve_local_model",
]
