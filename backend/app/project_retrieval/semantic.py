from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol


_TOKENS = re.compile(r"[a-z0-9_]+")


class EmbeddingUnavailable(RuntimeError):
    pass


class EmbeddingProvider(Protocol):
    identity: str
    version: str
    dimensions: int

    def transform_query(self, text: str) -> str: ...
    def transform_passage(self, text: str) -> str: ...
    def embed_query(self, text: str) -> tuple[float, ...]: ...
    def embed_passages(
        self, texts: tuple[str, ...]
    ) -> tuple[tuple[float, ...], ...]: ...
    def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]: ...


class DeterministicEmbeddingProvider:
    identity = "deterministic-hash-embedding"
    version = "v1"

    def __init__(self, dimensions: int = 64) -> None:
        if dimensions < 8 or dimensions > 1024:
            raise ValueError("invalid_embedding_dimensions")
        self.dimensions = dimensions

    def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple(self._one(text) for text in texts)

    def transform_query(self, text: str) -> str:
        return text

    def transform_passage(self, text: str) -> str:
        return text

    def embed_query(self, text: str) -> tuple[float, ...]:
        return self._one(text)

    def embed_passages(
        self, texts: tuple[str, ...]
    ) -> tuple[tuple[float, ...], ...]:
        return self.embed(texts)

    def _one(self, text: str) -> tuple[float, ...]:
        vector = [0.0] * self.dimensions
        for token in _TOKENS.findall(text.casefold()):
            digest = hashlib.sha256(token.encode()).digest()
            vector[int.from_bytes(digest[:4], "big") % self.dimensions] += 1.0
        magnitude = math.sqrt(sum(value * value for value in vector))
        return tuple(value / magnitude for value in vector) if magnitude else tuple(vector)


class UnavailableEmbeddingProvider:
    identity = "unavailable"
    version = "v1"
    dimensions = 0

    def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        del texts
        raise EmbeddingUnavailable("embedding_provider_unavailable")

    def transform_query(self, text: str) -> str:
        return text

    def transform_passage(self, text: str) -> str:
        return text

    def embed_query(self, text: str) -> tuple[float, ...]:
        del text
        raise EmbeddingUnavailable("embedding_provider_unavailable")

    def embed_passages(self, texts: tuple[str, ...]):
        return self.embed(texts)


def cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    if len(left) != len(right):
        raise ValueError("embedding_dimension_mismatch")
    score = sum(a * b for a, b in zip(left, right))
    if not math.isfinite(score):
        raise ValueError("invalid_semantic_score")
    return max(0.0, min(1.0, score))
