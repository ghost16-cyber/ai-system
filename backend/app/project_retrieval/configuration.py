from __future__ import annotations

import os
from typing import Literal

from pydantic import Field, model_validator

from backend.app.project_control.contracts import StrictModel
from backend.app.project_retrieval.provider_registry import (
    ProviderDevice,
    embedding_spec,
    reranker_spec,
)


class RetrievalProviderConfiguration(StrictModel):
    schema_version: Literal["astra.rag.provider-configuration.v1"] = (
        "astra.rag.provider-configuration.v1"
    )
    embedding_provider: Literal[
        "sentence_transformer", "deterministic", "unavailable"
    ] = "sentence_transformer"
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_device: ProviderDevice = ProviderDevice.AUTO
    embedding_batch_size: int = Field(default=16, ge=1, le=64)
    reranker_provider: Literal[
        "cross_encoder", "deterministic", "unavailable"
    ] = "cross_encoder"
    reranker_model: str = "cross-encoder/ms-marco-MiniLM-L6-v2"
    reranker_device: ProviderDevice = ProviderDevice.AUTO
    reranker_batch_size: int = Field(default=8, ge=1, le=20)
    local_files_only: Literal[True] = True
    provider_timeout_seconds: int = Field(default=60, ge=1, le=300)

    @model_validator(mode="after")
    def allowlisted_models_and_bounds(self) -> "RetrievalProviderConfiguration":
        if self.embedding_provider == "sentence_transformer":
            spec = embedding_spec(self.embedding_model)
            if self.embedding_batch_size > spec.maximum_batch_size:
                raise ValueError("rag_embedding_batch_size_exceeds_model_policy")
        if self.reranker_provider == "cross_encoder":
            spec = reranker_spec(self.reranker_model)
            if self.reranker_batch_size > spec.maximum_batch_size:
                raise ValueError("rag_reranker_batch_size_exceeds_model_policy")
        return self


def retrieval_configuration_from_environment() -> RetrievalProviderConfiguration:
    if os.getenv("ASTRA_RAG_LOCAL_FILES_ONLY", "1").strip().casefold() not in {
        "1", "true", "yes", "on",
    }:
        raise ValueError("rag_external_model_downloads_are_forbidden")
    return RetrievalProviderConfiguration(
        embedding_provider=os.getenv(
            "ASTRA_RAG_EMBEDDING_PROVIDER", "sentence_transformer"
        ),
        embedding_model=os.getenv(
            "ASTRA_RAG_EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5"
        ),
        embedding_device=os.getenv("ASTRA_RAG_EMBEDDING_DEVICE", "auto"),
        embedding_batch_size=int(
            os.getenv("ASTRA_RAG_EMBEDDING_BATCH_SIZE", "16")
        ),
        reranker_provider=os.getenv(
            "ASTRA_RAG_RERANKER_PROVIDER", "cross_encoder"
        ),
        reranker_model=os.getenv(
            "ASTRA_RAG_RERANKER_MODEL",
            "cross-encoder/ms-marco-MiniLM-L6-v2",
        ),
        reranker_device=os.getenv("ASTRA_RAG_RERANKER_DEVICE", "auto"),
        reranker_batch_size=int(
            os.getenv("ASTRA_RAG_RERANKER_BATCH_SIZE", "8")
        ),
        provider_timeout_seconds=int(
            os.getenv("ASTRA_RAG_PROVIDER_TIMEOUT_SECONDS", "60")
        ),
    )


__all__ = [
    "RetrievalProviderConfiguration",
    "retrieval_configuration_from_environment",
]
