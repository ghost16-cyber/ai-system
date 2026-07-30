from __future__ import annotations

import math
from pathlib import Path

import pytest

from backend.app.project_control.contracts import content_hash
from backend.app.project_retrieval.learned import (
    SentenceTransformerEmbeddingProvider,
    clear_learned_model_cache,
    loaded_model_cache_keys,
)
from backend.app.project_retrieval.provider_registry import (
    LocalModelResolution,
    ProviderDevice,
    embedding_spec,
    resolve_local_model,
)
from backend.app.project_retrieval.semantic import EmbeddingUnavailable


@pytest.fixture(autouse=True)
def _clear_model_cache():
    clear_learned_model_cache()
    yield
    clear_learned_model_cache()


class _Array:
    def __init__(self, values):
        self.values = values

    def tolist(self):
        return self.values


class _EmbeddingModel:
    def __init__(self, dimensions=384, malformed=None):
        self.dimensions = dimensions
        self.malformed = malformed
        self.calls = []

    def encode(self, texts, **kwargs):
        self.calls.append((tuple(texts), kwargs))
        if self.malformed is not None:
            return _Array(self.malformed)
        vector = [0.0] * self.dimensions
        vector[0] = 1.0
        return _Array([vector[:] for _ in texts])


def _resolution(tmp_path: Path, model_id: str, revision: str = "abc123"):
    snapshot = tmp_path / revision
    snapshot.mkdir(parents=True)
    return LocalModelResolution(
        model_id=model_id,
        configured_revision="main",
        locally_cached=True,
        resolved_revision=revision,
        snapshot_path=snapshot.as_posix(),
        effective_identity=content_hash([model_id, revision]),
    )


def test_embedding_registry_rejects_arbitrary_models() -> None:
    assert embedding_spec("BAAI/bge-small-en-v1.5").dimensions == 384
    assert embedding_spec("sentence-transformers/all-MiniLM-L6-v2").dimensions == 384
    with pytest.raises(ValueError, match="not_allowlisted"):
        embedding_spec("user/arbitrary-model")


def test_local_revision_resolution_never_fabricates_a_snapshot(tmp_path: Path) -> None:
    spec = embedding_spec("BAAI/bge-small-en-v1.5")
    model_root = tmp_path / "models--BAAI--bge-small-en-v1.5"
    snapshot = model_root / "snapshots" / "commit-123"
    snapshot.mkdir(parents=True)
    (model_root / "refs").mkdir()
    (model_root / "refs" / "main").write_text("commit-123", encoding="utf-8")
    resolved = resolve_local_model(spec, cache_root=tmp_path)
    assert resolved.locally_cached is True
    assert resolved.resolved_revision == "commit-123"
    missing = resolve_local_model(
        embedding_spec("sentence-transformers/all-MiniLM-L6-v2"),
        cache_root=tmp_path,
    )
    assert missing.locally_cached is False
    assert missing.resolved_revision == "unresolved-local-cache"


def test_bge_transform_batch_order_dimension_and_normalization(tmp_path: Path) -> None:
    spec = embedding_spec("BAAI/bge-small-en-v1.5")
    model = _EmbeddingModel()
    provider = SentenceTransformerEmbeddingProvider(
        spec,
        _resolution(tmp_path, spec.model_id),
        requested_device=ProviderDevice.CPU,
        batch_size=2,
        loader=lambda path, device: model,
    )
    query = provider.embed_query("lifecycle authority")
    passages = provider.embed_passages(("first", "second"))
    assert len(query) == 384
    assert all(len(item) == 384 for item in passages)
    assert math.isclose(sum(value * value for value in query), 1.0)
    assert model.calls[0][0][0].startswith("Represent this sentence")
    assert model.calls[1][0] == ("first", "second")
    assert model.calls[1][1]["normalize_embeddings"] is True
    assert provider.actual_device == "cpu"


@pytest.mark.parametrize(
    "malformed,code",
    [
        ([[1.0, 0.0]], "embedding_dimension"),
        ([[float("nan")] * 384], "non_finite"),
        ([[0.5] * 384], "not_normalized"),
    ],
)
def test_invalid_embedding_output_fails_closed(
    tmp_path: Path, malformed, code: str
) -> None:
    spec = embedding_spec("BAAI/bge-small-en-v1.5")
    provider = SentenceTransformerEmbeddingProvider(
        spec,
        _resolution(tmp_path, spec.model_id),
        requested_device=ProviderDevice.CPU,
        loader=lambda path, device: _EmbeddingModel(malformed=malformed),
    )
    with pytest.raises(EmbeddingUnavailable):
        provider.embed_passages(("text",))
    assert code in str(provider.last_failure)


def test_lazy_cache_is_bounded_and_clearable(tmp_path: Path) -> None:
    clear_learned_model_cache()
    models = []
    for index in range(3):
        spec = embedding_spec("BAAI/bge-small-en-v1.5")
        model = _EmbeddingModel()
        provider = SentenceTransformerEmbeddingProvider(
            spec,
            _resolution(tmp_path / str(index), spec.model_id, f"rev-{index}"),
            requested_device=ProviderDevice.CPU,
            loader=lambda path, device, model=model: model,
        )
        assert provider.call_count == 0
        provider.embed_passages(("text",))
        models.append(model)
    assert len(loaded_model_cache_keys()) == 2
    clear_learned_model_cache()
    assert loaded_model_cache_keys() == ()


def test_missing_cached_model_fails_before_loader_submission(tmp_path: Path) -> None:
    spec = embedding_spec("BAAI/bge-small-en-v1.5")
    called = False

    def loader(path, device):
        nonlocal called
        called = True

    provider = SentenceTransformerEmbeddingProvider(
        spec,
        LocalModelResolution(
            model_id=spec.model_id,
            configured_revision="main",
            locally_cached=False,
            resolved_revision="unresolved-local-cache",
            effective_identity="f" * 64,
        ),
        loader=loader,
    )
    with pytest.raises(EmbeddingUnavailable, match="not_cached"):
        provider.embed_passages(("text",))
    assert called is False


def test_auto_device_falls_back_to_cpu_only_when_cuda_fails_before_work(
    tmp_path: Path,
) -> None:
    spec = embedding_spec("BAAI/bge-small-en-v1.5")
    devices = []

    def loader(path, device):
        devices.append(device)
        if device == "cuda":
            raise RuntimeError("CUDA out of memory")
        return _EmbeddingModel()

    provider = SentenceTransformerEmbeddingProvider(
        spec,
        _resolution(tmp_path, spec.model_id),
        requested_device=ProviderDevice.AUTO,
        loader=loader,
        cuda_admitted=lambda: True,
    )
    vector = provider.embed_query("query")
    assert len(vector) == 384
    assert devices == ["cuda", "cpu"]
    assert provider.actual_device == "cpu"
    assert provider.last_failure == "cuda_failed_cpu_fallback"
