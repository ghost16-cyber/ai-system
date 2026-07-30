from __future__ import annotations

import pytest

from backend.app.local_runtime import build_execution_profile


def _context(
    *,
    cuda_available: bool = True,
    low_vram_mode: bool = True,
    faiss_available: bool = True,
) -> dict:
    return {
        "hardware": {
            "gpu": {
                "cuda_available": cuda_available,
                "vram_total_mb": 4096 if low_vram_mode else 16 * 1024,
            }
        },
        "policy": {
            "low_vram_mode": low_vram_mode,
            "prefer_quantized_models": True,
            "cpu_fallback_allowed": True,
            "max_recommended_local_model_billion_params": (
                3.0 if low_vram_mode else 7.0
            ),
        },
        "task_optimization": {
            "settings": {
                "batch_size": [2, 4] if low_vram_mode else [8, 32],
            }
        },
        "tools": [
            {"name": "ollama", "available": "available"},
            {"name": "torch", "available": "available"},
            {"name": "sklearn", "available": "available"},
            {"name": "joblib", "available": "available"},
            {"name": "sentence_transformers", "available": "available"},
            {
                "name": "faiss",
                "available": "available" if faiss_available else "missing",
            },
        ],
    }


def test_builds_low_vram_local_slm_execution_profile():
    profile = build_execution_profile(
        task="local_slm",
        runtime_context=_context(),
        active_runtime_plan={
            "strategy": "quantized_inference",
            "use_quantized_model": True,
        },
    )

    assert profile.task_type == "local_slm"
    assert profile.runtime == "ollama"
    assert profile.device == "cuda"
    assert profile.settings["model_size_limit_billion_params"] == 3.0
    assert profile.settings["quantization_required"] is True
    assert profile.settings["max_context_tokens"] == 4096
    assert profile.settings["cpu_fallback_allowed"] is True


def test_builds_pytorch_training_execution_profile():
    profile = build_execution_profile(
        task="pytorch_training",
        runtime_context=_context(),
        active_runtime_plan={
            "strategy": "small_model_training",
            "device": "cuda",
        },
    )

    assert profile.runtime == "pytorch"
    assert profile.device == "cuda"
    assert profile.settings["batch_size_range"] == [2, 4]
    assert profile.settings["initial_batch_size"] == 2
    assert profile.settings["gradient_accumulation"] is True
    assert profile.settings["mixed_precision"] is True
    assert profile.settings["checkpoint_every_steps"] == 250


def test_pytorch_profile_uses_cpu_when_cuda_is_missing():
    profile = build_execution_profile(
        task="pytorch_training",
        runtime_context=_context(cuda_available=False),
        active_runtime_plan={
            "strategy": "small_model_training",
            "device": "cpu",
        },
    )

    assert profile.device == "cpu"
    assert profile.settings["mixed_precision"] is False
    assert profile.settings["pin_memory"] is False


def test_builds_rag_profile_with_faiss_and_embedding_settings():
    profile = build_execution_profile(
        task="rag",
        runtime_context=_context(),
        active_runtime_plan={"strategy": "embedding_retrieval"},
    )

    assert profile.task_type == "rag"
    assert profile.runtime == "rag_pipeline"
    assert profile.device == "hybrid"
    assert profile.settings["embedding_backend"] == "sentence_transformers"
    assert profile.settings["faiss_enabled"] is True
    assert profile.settings["vector_backend"] == "faiss"
    assert profile.settings["chunk_size_tokens"] == 512
    assert profile.settings["top_k"] == 5
    assert profile.settings["reranking_allowed"] is False


def test_rag_profile_falls_back_when_faiss_is_missing():
    profile = build_execution_profile(
        task="rag",
        runtime_context=_context(faiss_available=False),
        active_runtime_plan={"strategy": "embedding_retrieval"},
    )

    assert profile.settings["faiss_enabled"] is False
    assert profile.settings["vector_backend"] == "numpy"


def test_builds_cpu_classical_ml_profile_with_joblib_persistence():
    profile = build_execution_profile(
        task="classical_ml",
        runtime_context=_context(cuda_available=False),
        active_runtime_plan={"strategy": "sklearn_pipeline"},
    )

    assert profile.runtime == "scikit_learn"
    assert profile.device == "cpu"
    assert profile.settings["sklearn_pipeline_allowed"] is True
    assert profile.settings["gpu_required"] is False
    assert profile.settings["joblib_persistence_allowed"] is True


def test_active_downgraded_strategy_controls_effective_profile():
    profile = build_execution_profile(
        task="large model fine-tuning",
        runtime_context=_context(),
        active_runtime_plan={"strategy": "rag", "use_quantized_model": True},
    )

    assert profile.task_type == "rag"
    assert profile.runtime == "rag_pipeline"


def test_execution_profile_requires_active_validated_plan():
    with pytest.raises(ValueError, match="active validated runtime plan"):
        build_execution_profile(
            task="local_slm",
            runtime_context=_context(),
            active_runtime_plan={},
        )
