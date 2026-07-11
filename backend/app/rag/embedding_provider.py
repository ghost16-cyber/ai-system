from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Provider-neutral interface for corpus and query embeddings."""

    @property
    def provider_name(self) -> str: ...

    @property
    def model_name(self) -> str: ...

    @property
    def dimension(self) -> int: ...

    @property
    def normalization(self) -> str: ...

    def configuration(self) -> dict[str, Any]: ...

    def embed_texts(self, texts: list[str]) -> list[list[float]]: ...
