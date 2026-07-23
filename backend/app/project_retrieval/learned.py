from __future__ import annotations

import importlib.util
import math
import threading
from collections import OrderedDict
from collections.abc import Callable
from time import perf_counter
from typing import Any

from backend.app.project_retrieval.contracts import (
    RetrievalCandidate,
    RetrievalProviderReadiness,
)
from backend.app.project_retrieval.provider_registry import (
    LocalModelResolution,
    ProviderDevice,
    RetrievalModelSpec,
)
from backend.app.project_retrieval.reranking import RerankerUnavailable
from backend.app.project_retrieval.semantic import EmbeddingUnavailable


MAX_LOADED_RETRIEVAL_MODELS = 2
_MODEL_CACHE: OrderedDict[tuple[str, str, str, str], Any] = OrderedDict()
_MODEL_CACHE_LOCK = threading.RLock()
_Loader = Callable[[str, str], Any]


class LearnedProviderFailure(RuntimeError):
    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        super().__init__(message or code)


def clear_learned_model_cache() -> None:
    with _MODEL_CACHE_LOCK:
        _MODEL_CACHE.clear()


def loaded_model_cache_keys() -> tuple[tuple[str, str, str, str], ...]:
    with _MODEL_CACHE_LOCK:
        return tuple(_MODEL_CACHE)


class SentenceTransformerEmbeddingProvider:
    provider_type = "sentence_transformer"

    def __init__(
        self,
        spec: RetrievalModelSpec,
        resolution: LocalModelResolution,
        *,
        requested_device: ProviderDevice = ProviderDevice.AUTO,
        batch_size: int | None = None,
        loader: _Loader | None = None,
        cuda_admitted: Callable[[], bool] | None = None,
        timeout_seconds: int = 60,
    ) -> None:
        if spec.provider_type != self.provider_type or spec.dimensions is None:
            raise ValueError("invalid_sentence_transformer_spec")
        self.spec = spec
        self.resolution = resolution
        self.requested_device = ProviderDevice(requested_device)
        self.batch_size = batch_size or spec.default_batch_size
        if not 1 <= self.batch_size <= spec.maximum_batch_size:
            raise ValueError("embedding_batch_size_exceeds_policy")
        self.dimensions = spec.dimensions
        self.identity = (
            f"sentence-transformer:{spec.model_id}@{resolution.resolved_revision}:"
            f"{resolution.effective_identity}"
        )
        self.version = spec.transform_policy_version
        self.actual_device: str | None = None
        self.last_failure: str | None = None
        self.call_count = 0
        self._loader = loader or _sentence_transformer_loader
        self._cuda_admitted = cuda_admitted
        self.timeout_seconds = max(1, min(int(timeout_seconds), 300))

    def transform_query(self, text: str) -> str:
        return f"{self.spec.query_prefix}{text}"

    def transform_passage(self, text: str) -> str:
        return f"{self.spec.passage_prefix}{text}"

    def embed_query(self, text: str) -> tuple[float, ...]:
        return self._encode((self.transform_query(text),))[0]

    def embed_passages(
        self, texts: tuple[str, ...]
    ) -> tuple[tuple[float, ...], ...]:
        return self._encode(tuple(self.transform_passage(text) for text in texts))

    def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return self.embed_passages(texts)

    def readiness(self) -> RetrievalProviderReadiness:
        package = importlib.util.find_spec("sentence_transformers") is not None
        reason = None
        if not package:
            reason = "sentence_transformers_package_unavailable"
        elif not self.resolution.locally_cached:
            reason = "embedding_model_not_cached"
        return RetrievalProviderReadiness(
            provider_kind="embedding",
            provider_type=self.provider_type,
            configured_model_id=self.spec.model_id,
            resolved_revision=self.resolution.resolved_revision,
            effective_identity=self.identity,
            package_installed=package,
            model_present_locally=self.resolution.locally_cached,
            ready=reason is None,
            reason=reason,
            requested_device=self.requested_device.value,
            admitted_device=self.actual_device,
            dimensions=self.dimensions,
            maximum_sequence_length=self.spec.maximum_sequence_length,
            batch_size=self.batch_size,
            maximum_batch_size=self.spec.maximum_batch_size,
            normalization_required=self.spec.normalize_embeddings,
            estimated_ram_bytes=self.spec.estimated_ram_bytes,
            estimated_vram_bytes=self.spec.estimated_vram_bytes,
            last_failure=self.last_failure,
        )

    def _encode(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        if not texts:
            return ()
        self.call_count += 1
        if not self.resolution.locally_cached or not self.resolution.snapshot_path:
            self.last_failure = "embedding_model_not_cached"
            raise EmbeddingUnavailable(self.last_failure)
        device = self._select_device()
        started = perf_counter()
        try:
            model = _cached_model(
                self.provider_type,
                self.resolution,
                device,
                self._loader,
            )
            output = model.encode(
                list(texts),
                batch_size=self.batch_size,
                normalize_embeddings=self.spec.normalize_embeddings,
                convert_to_numpy=True,
                show_progress_bar=False,
            )
            vectors = _vectors(output, len(texts), self.dimensions)
            if self.spec.normalize_embeddings:
                _validate_normalized(vectors)
            if perf_counter() - started > self.timeout_seconds:
                raise LearnedProviderFailure("embedding_timeout")
            self.actual_device = device
            self.last_failure = None
            return vectors
        except Exception as exc:
            if device == "cuda" and self.requested_device == ProviderDevice.AUTO:
                try:
                    model = _cached_model(
                        self.provider_type,
                        self.resolution,
                        "cpu",
                        self._loader,
                    )
                    output = model.encode(
                        list(texts),
                        batch_size=self.batch_size,
                        normalize_embeddings=self.spec.normalize_embeddings,
                        convert_to_numpy=True,
                        show_progress_bar=False,
                    )
                    vectors = _vectors(output, len(texts), self.dimensions)
                    if self.spec.normalize_embeddings:
                        _validate_normalized(vectors)
                    self.actual_device = "cpu"
                    self.last_failure = "cuda_failed_cpu_fallback"
                    return vectors
                except Exception as cpu_exc:
                    exc = cpu_exc
            self.last_failure = _safe_failure(exc, "embedding_provider_failure")
            raise EmbeddingUnavailable(self.last_failure) from exc

    def _select_device(self) -> str:
        if self.requested_device == ProviderDevice.CPU:
            return "cpu"
        if self.requested_device == ProviderDevice.CUDA:
            if self._cuda_admitted is not None and not self._cuda_admitted():
                raise EmbeddingUnavailable("cuda_not_admitted")
            return "cuda"
        if self._cuda_admitted is not None and self._cuda_admitted():
            return "cuda"
        return "cpu"


class CrossEncoderReranker:
    provider_type = "cross_encoder"

    def __init__(
        self,
        spec: RetrievalModelSpec,
        resolution: LocalModelResolution,
        *,
        requested_device: ProviderDevice = ProviderDevice.AUTO,
        batch_size: int | None = None,
        loader: _Loader | None = None,
        cuda_admitted: Callable[[], bool] | None = None,
        timeout_seconds: int = 60,
    ) -> None:
        if spec.provider_type != self.provider_type:
            raise ValueError("invalid_cross_encoder_spec")
        self.spec = spec
        self.resolution = resolution
        self.requested_device = ProviderDevice(requested_device)
        self.batch_size = batch_size or spec.default_batch_size
        if not 1 <= self.batch_size <= min(20, spec.maximum_batch_size):
            raise ValueError("reranker_batch_size_exceeds_policy")
        self.identity = (
            f"cross-encoder:{spec.model_id}@{resolution.resolved_revision}:"
            f"{resolution.effective_identity}"
        )
        self.version = spec.transform_policy_version
        self.actual_device: str | None = None
        self.last_failure: str | None = None
        self.call_count = 0
        self._loader = loader or _cross_encoder_loader
        self._cuda_admitted = cuda_admitted
        self.timeout_seconds = max(1, min(int(timeout_seconds), 300))

    def readiness(self) -> RetrievalProviderReadiness:
        package = importlib.util.find_spec("sentence_transformers") is not None
        reason = None
        if not package:
            reason = "sentence_transformers_package_unavailable"
        elif not self.resolution.locally_cached:
            reason = "reranker_model_not_cached"
        return RetrievalProviderReadiness(
            provider_kind="reranker",
            provider_type=self.provider_type,
            configured_model_id=self.spec.model_id,
            resolved_revision=self.resolution.resolved_revision,
            effective_identity=self.identity,
            package_installed=package,
            model_present_locally=self.resolution.locally_cached,
            ready=reason is None,
            reason=reason,
            requested_device=self.requested_device.value,
            admitted_device=self.actual_device,
            dimensions=None,
            maximum_sequence_length=self.spec.maximum_sequence_length,
            batch_size=self.batch_size,
            maximum_batch_size=min(20, self.spec.maximum_batch_size),
            normalization_required=False,
            estimated_ram_bytes=self.spec.estimated_ram_bytes,
            estimated_vram_bytes=self.spec.estimated_vram_bytes,
            last_failure=self.last_failure,
        )

    def rerank(
        self,
        query: str,
        candidates: tuple[tuple[RetrievalCandidate, str], ...],
    ) -> dict[str, float]:
        if len(candidates) > 20:
            raise RerankerUnavailable("reranker_candidate_limit_exceeded")
        self.call_count += 1
        if not self.resolution.locally_cached or not self.resolution.snapshot_path:
            self.last_failure = "reranker_model_not_cached"
            raise RerankerUnavailable(self.last_failure)
        device = self._select_device()
        pairs = [(query, text) for _, text in candidates]
        started = perf_counter()
        try:
            model = _cached_model(
                self.provider_type, self.resolution, device, self._loader
            )
            output = model.predict(
                pairs,
                batch_size=self.batch_size,
                show_progress_bar=False,
            )
            scores = _scores(output, len(candidates))
            if perf_counter() - started > self.timeout_seconds:
                raise LearnedProviderFailure("reranker_timeout")
            self.actual_device = device
            self.last_failure = None
            return {
                candidate.chunk_id: score
                for (candidate, _), score in zip(candidates, scores, strict=True)
            }
        except Exception as exc:
            if device == "cuda" and self.requested_device == ProviderDevice.AUTO:
                try:
                    model = _cached_model(
                        self.provider_type, self.resolution, "cpu", self._loader
                    )
                    scores = _scores(
                        model.predict(
                            pairs,
                            batch_size=self.batch_size,
                            show_progress_bar=False,
                        ),
                        len(candidates),
                    )
                    self.actual_device = "cpu"
                    self.last_failure = "cuda_failed_cpu_fallback"
                    return {
                        candidate.chunk_id: score
                        for (candidate, _), score in zip(
                            candidates, scores, strict=True
                        )
                    }
                except Exception as cpu_exc:
                    exc = cpu_exc
            self.last_failure = _safe_failure(exc, "reranker_provider_failure")
            raise RerankerUnavailable(self.last_failure) from exc

    def _select_device(self) -> str:
        if self.requested_device == ProviderDevice.CPU:
            return "cpu"
        if self.requested_device == ProviderDevice.CUDA:
            if self._cuda_admitted is not None and not self._cuda_admitted():
                raise RerankerUnavailable("cuda_not_admitted")
            return "cuda"
        if self._cuda_admitted is not None and self._cuda_admitted():
            return "cuda"
        return "cpu"


def _cached_model(
    provider_type: str,
    resolution: LocalModelResolution,
    device: str,
    loader: _Loader,
) -> Any:
    if not resolution.snapshot_path:
        raise LearnedProviderFailure("model_not_cached")
    key = (
        provider_type,
        resolution.effective_identity,
        resolution.resolved_revision,
        device,
    )
    with _MODEL_CACHE_LOCK:
        if key in _MODEL_CACHE:
            model = _MODEL_CACHE.pop(key)
            _MODEL_CACHE[key] = model
            return model
        model = loader(resolution.snapshot_path, device)
        _MODEL_CACHE[key] = model
        while len(_MODEL_CACHE) > MAX_LOADED_RETRIEVAL_MODELS:
            _MODEL_CACHE.popitem(last=False)
        return model


def _sentence_transformer_loader(snapshot_path: str, device: str) -> Any:
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(
        snapshot_path,
        device=device,
        local_files_only=True,
    )


def _cross_encoder_loader(snapshot_path: str, device: str) -> Any:
    from sentence_transformers import CrossEncoder

    return CrossEncoder(
        snapshot_path,
        device=device,
        local_files_only=True,
    )


def _vectors(
    output: Any, expected_count: int, expected_dimensions: int
) -> tuple[tuple[float, ...], ...]:
    raw = output.tolist() if hasattr(output, "tolist") else output
    try:
        vectors = tuple(tuple(float(value) for value in row) for row in raw)
    except (TypeError, ValueError) as exc:
        raise LearnedProviderFailure("malformed_embedding_output") from exc
    if len(vectors) != expected_count:
        raise LearnedProviderFailure("embedding_count_mismatch")
    if any(len(vector) != expected_dimensions for vector in vectors):
        raise LearnedProviderFailure("embedding_dimension_mismatch")
    if any(not math.isfinite(value) for vector in vectors for value in vector):
        raise LearnedProviderFailure("non_finite_embedding")
    return vectors


def _validate_normalized(
    vectors: tuple[tuple[float, ...], ...], *, tolerance: float = 1e-3
) -> None:
    for vector in vectors:
        magnitude = math.sqrt(sum(value * value for value in vector))
        if abs(magnitude - 1.0) > tolerance:
            raise LearnedProviderFailure("embedding_not_normalized")


def _scores(output: Any, expected_count: int) -> tuple[float, ...]:
    raw = output.tolist() if hasattr(output, "tolist") else output
    try:
        values = tuple(float(value) for value in raw)
    except (TypeError, ValueError) as exc:
        raise LearnedProviderFailure("malformed_reranker_output") from exc
    if len(values) != expected_count:
        raise LearnedProviderFailure("reranker_count_mismatch")
    if any(not math.isfinite(value) for value in values):
        raise LearnedProviderFailure("non_finite_reranker_score")
    return values


def _safe_failure(exc: Exception, default: str) -> str:
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code:
        return code[:300]
    name = type(exc).__name__.casefold()
    if "outofmemory" in name or "out_of_memory" in str(exc).casefold():
        return "cuda_out_of_memory"
    return default


__all__ = [
    "CrossEncoderReranker",
    "LearnedProviderFailure",
    "MAX_LOADED_RETRIEVAL_MODELS",
    "SentenceTransformerEmbeddingProvider",
    "clear_learned_model_cache",
    "loaded_model_cache_keys",
]
