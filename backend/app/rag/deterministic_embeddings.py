from __future__ import annotations

import hashlib
import math
import re
from typing import Any


TOKEN_PATTERN = re.compile(r"[a-z0-9_]+")


class DeterministicEmbeddingProvider:
    """Offline hashing embeddings for tests, not production semantic search."""

    provider_name = "deterministic-local"
    model_name = "sha256-token-frequency-v1"
    normalization = "l2"

    def __init__(self, dimension: int = 64) -> None:
        if dimension <= 0:
            raise ValueError("embedding dimension must be greater than zero")
        self._dimension = dimension

    @property
    def dimension(self) -> int:
        return self._dimension

    def configuration(self) -> dict[str, Any]:
        return {
            "provider": self.provider_name,
            "model": self.model_name,
            "dimension": self.dimension,
            "normalization": self.normalization,
            "production_quality": False,
        }

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension

        for token in TOKEN_PATTERN.findall(text.lower()):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            bucket = int.from_bytes(digest[:8], "big") % self.dimension
            vector[bucket] += 1.0

        magnitude = math.sqrt(sum(value * value for value in vector))
        if magnitude:
            vector = [value / magnitude for value in vector]

        return vector
