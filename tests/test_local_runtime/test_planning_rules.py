from __future__ import annotations

from backend.app.hardware_ai_optimizer import (
    GPUInfo,
    HardwareReport,
    PyTorchInfo,
    RAMInfo,
    StorageInfo,
)
from backend.app.local_runtime import (
    ToolStatus,
    build_runtime_policy,
    validate_task_plan,
)


def _report(*, cuda_available: bool = True, vram_mb: int = 4096) -> HardwareReport:
    return HardwareReport(
        cpu_name="Test CPU",
        cpu_count=12,
        ram=RAMInfo(total_mb=32 * 1024, available_mb=24 * 1024),
        gpu=GPUInfo(
            name="Test GPU" if cuda_available else None,
            cuda_available=cuda_available,
            vram_total_mb=vram_mb if cuda_available else None,
            vram_free_mb=max(vram_mb - 1024, 0) if cuda_available else None,
            source="torch" if cuda_available else "none",
        ),
        storage=StorageInfo(path="/workspace", total_mb=512 * 1024, free_mb=200 * 1024),
        pytorch=PyTorchInfo(installed=True, version="2.3.0", cuda_version="12.1"),
    )


def _tools() -> list[ToolStatus]:
    return [
        ToolStatus(name="torch", kind="python_package", available="available"),
        ToolStatus(name="sklearn", kind="python_package", available="available"),
        ToolStatus(name="faiss", kind="python_package", available="available"),
    ]


def _context(*, cuda_available: bool = True, vram_mb: int = 4096) -> dict:
    report = _report(cuda_available=cuda_available, vram_mb=vram_mb)
    policy = build_runtime_policy(report, _tools())
    return {
        "hardware": report.model_dump(mode="json"),
        "policy": policy.model_dump(mode="json"),
    }


def test_blocks_full_finetuning_on_low_vram():
    result = validate_task_plan(
        task="large_model_finetuning",
        requested_plan={
            "strategy": "full_finetuning",
            "model_size_billion_params": 7,
            "requires_gpu": True,
        },
        runtime_context=_context(),
    )

    assert result.allowed is False
    assert result.decision == "downgrade"
    assert "full_finetuning" in result.blocked_signals
    assert "large_model_training" in result.blocked_signals
    assert result.recommended_plan["strategy"] == "rag"
    assert result.recommended_plan["use_quantized_model"] is True


def test_downgrades_large_model_inference_to_quantized():
    result = validate_task_plan(
        task="local_slm",
        requested_plan={
            "strategy": "local_inference",
            "model_size_billion_params": 8,
        },
        runtime_context=_context(),
    )

    assert result.allowed is False
    assert result.decision == "downgrade"
    assert result.blocked_signals == ["large_local_model"]
    assert result.recommended_plan["strategy"] == "quantized_inference"
    assert result.recommended_plan["use_quantized_model"] is True
    assert result.recommended_plan["model_size_billion_params"] == 3.0


def test_allows_classical_ml_without_gpu():
    result = validate_task_plan(
        task="classical_ml",
        requested_plan={
            "strategy": "sklearn_pipeline",
            "requires_gpu": False,
            "device": "cpu",
        },
        runtime_context=_context(cuda_available=False),
    )

    assert result.allowed is True
    assert result.decision == "allow"
    assert result.blocked_signals == []


def test_uses_cpu_fallback_when_cuda_missing():
    result = validate_task_plan(
        task="pytorch_training",
        requested_plan={
            "strategy": "small_model_training",
            "requires_gpu": True,
            "device": "cuda",
        },
        runtime_context=_context(cuda_available=False),
    )

    assert result.allowed is False
    assert result.decision == "downgrade"
    assert result.blocked_signals == ["cuda_unavailable"]
    assert result.recommended_plan["device"] == "cpu"
    assert result.recommended_plan["requires_gpu"] is False
    assert result.recommended_plan["allow_cpu_fallback"] is True


def test_blocks_gpu_only_plan_when_no_fallback_is_permitted():
    context = _context(cuda_available=False)
    context["policy"]["cpu_fallback_allowed"] = False
    context["policy"]["prefer_cpu_fallback"] = False

    result = validate_task_plan(
        task="pytorch_training",
        requested_plan={
            "strategy": "gpu_only_training",
            "requires_gpu": True,
            "device": "cuda",
        },
        runtime_context=context,
    )

    assert result.allowed is False
    assert result.decision == "block"
    assert result.blocked_signals == ["cuda_unavailable"]
    assert result.recommended_plan == {}


def test_high_vram_allows_wider_training_plan():
    result = validate_task_plan(
        task="pytorch_training",
        requested_plan={
            "strategy": "full_finetuning",
            "model_size_billion_params": 3,
            "requires_gpu": True,
            "device": "cuda",
        },
        runtime_context=_context(vram_mb=16 * 1024),
    )

    assert result.allowed is True
    assert result.decision == "allow"
    assert result.recommended_plan["strategy"] == "full_finetuning"


def test_rag_task_rejects_finetuning_first_plan():
    result = validate_task_plan(
        task="rag",
        requested_plan={
            "strategy": "fine_tuning",
            "requires_gpu": False,
        },
        runtime_context=_context(),
    )

    assert result.allowed is False
    assert result.decision == "downgrade"
    assert "finetuning_first_for_rag" in result.blocked_signals
    assert result.recommended_plan["strategy"] == "embedding_retrieval"
    assert result.recommended_plan["embedding_workflow"] is True
    assert result.recommended_plan["use_fine_tuning"] is False
