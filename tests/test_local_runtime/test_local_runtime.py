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
    build_capability_profile,
    build_runtime_policy,
    classify_task,
    optimize_for_task,
)


def _low_vram_report(cuda_available: bool = True) -> HardwareReport:
    return HardwareReport(
        cpu_name="Intel i5-12500H",
        cpu_count=12,
        ram=RAMInfo(total_mb=32 * 1024, available_mb=20 * 1024),
        gpu=GPUInfo(
            name="RTX 3050 Laptop GPU" if cuda_available else None,
            cuda_available=cuda_available,
            vram_total_mb=4096 if cuda_available else None,
            vram_free_mb=3072 if cuda_available else None,
            source="torch" if cuda_available else "none",
        ),
        storage=StorageInfo(path="/workspace", total_mb=512 * 1024, free_mb=200 * 1024),
        pytorch=PyTorchInfo(installed=True, version="2.3.0", cuda_version="12.1"),
    )


def _high_vram_report() -> HardwareReport:
    return HardwareReport(
        cpu_name="Workstation CPU",
        cpu_count=16,
        ram=RAMInfo(total_mb=64 * 1024, available_mb=48 * 1024),
        gpu=GPUInfo(
            name="RTX 4080",
            cuda_available=True,
            vram_total_mb=16 * 1024,
            vram_free_mb=14 * 1024,
            source="torch",
        ),
        storage=StorageInfo(path="/workspace", total_mb=1024 * 1024, free_mb=800 * 1024),
        pytorch=PyTorchInfo(installed=True, version="2.3.0", cuda_version="12.1"),
    )


def _tools() -> list[ToolStatus]:
    return [
        ToolStatus(name="python", kind="command", available="available"),
        ToolStatus(name="git", kind="command", available="available"),
        ToolStatus(name="ollama", kind="command", available="available"),
        ToolStatus(name="torch", kind="python_package", available="available"),
        ToolStatus(name="sklearn", kind="python_package", available="available"),
        ToolStatus(name="transformers", kind="python_package", available="available"),
        ToolStatus(name="sentence_transformers", kind="python_package", available="available"),
        ToolStatus(name="faiss", kind="python_package", available="available"),
    ]


def test_runtime_policy_marks_four_gb_gpu_as_low_vram():
    policy = build_runtime_policy(_low_vram_report(), _tools())

    assert policy.low_vram_mode is True
    assert policy.prefer_quantized_models is True
    assert policy.avoid_large_models is True
    assert policy.prefer_rag_over_finetuning is True
    assert policy.cpu_fallback_allowed is True
    assert policy.max_recommended_local_model_billion_params == 3.0
    assert any("4GB" in note for note in policy.notes)


def test_phase11_local_slm_low_vram_policy_is_safe_for_this_laptop():
    report = _low_vram_report()
    tools = _tools()
    policy = build_runtime_policy(report, tools)

    optimization = optimize_for_task("local_slm", report, tools, policy)

    assert policy.low_vram_mode is True
    assert policy.prefer_quantized_models is True
    assert policy.avoid_large_models is True
    assert policy.cpu_fallback_allowed is True
    assert optimization.settings["prefer_quantized"] is True
    assert optimization.settings["avoid_large_models"] is True
    assert optimization.settings["cpu_fallback_allowed"] is True


def test_capability_profile_limits_large_finetuning_on_low_vram():
    capabilities = build_capability_profile(_low_vram_report(), _tools())
    by_name = {capability.name: capability for capability in capabilities}

    assert by_name["local_slm_inference"].status == "limited"
    assert by_name["pytorch_cuda_training"].status == "limited"
    assert by_name["rag_workflows"].status == "good"
    assert by_name["large_model_finetuning"].status == "unavailable"
    assert "batch size 2-4" in by_name["pytorch_cuda_training"].limits


def test_phase11_rag_prefers_embedding_workflow_and_discourages_finetuning():
    report = _low_vram_report()
    tools = _tools()
    policy = build_runtime_policy(report, tools)

    optimization = optimize_for_task("rag", report, tools, policy)

    assert optimization.task_type == "rag"
    assert optimization.local_fit == "good"
    assert optimization.settings["faiss_available"] is True
    assert optimization.settings["prefer_rag"] is True
    assert optimization.settings["embedding_workflow_recommended"] is True
    assert optimization.settings["fine_tuning_discouraged"] is True
    assert "sentence_transformers" in optimization.recommended_tools


def test_task_optimizer_selects_safe_training_settings_for_low_vram_gpu():
    report = _low_vram_report()
    tools = _tools()
    policy = build_runtime_policy(report, tools)

    optimization = optimize_for_task("train this PyTorch image model", report, tools, policy)

    assert optimization.task_type == "pytorch_training"
    assert optimization.local_fit == "limited"
    assert optimization.settings["batch_size"] == [2, 4]
    assert optimization.settings["cuda_available"] is True
    assert optimization.settings["mixed_precision"] is True
    assert optimization.settings["gradient_accumulation"] is True
    assert optimization.settings["large_model_training_discouraged"] is True
    assert "torch" in optimization.recommended_tools


def test_phase11_pytorch_training_changes_for_high_vram_context():
    report = _high_vram_report()
    tools = _tools()
    policy = build_runtime_policy(report, tools)

    optimization = optimize_for_task("pytorch_training", report, tools, policy)

    assert policy.low_vram_mode is False
    assert policy.avoid_large_models is False
    assert optimization.local_fit == "good"
    assert optimization.settings["batch_size"] == [8, 32]
    assert optimization.settings["gradient_accumulation"] is False
    assert optimization.settings["large_model_training_discouraged"] is False


def test_task_optimizer_prefers_quantized_small_local_slm():
    report = _low_vram_report()
    tools = _tools()
    policy = build_runtime_policy(report, tools)

    optimization = optimize_for_task("run a local SLM for code help", report, tools, policy)

    assert optimization.task_type == "local_slm"
    assert optimization.local_fit == "limited"
    assert optimization.settings["max_model_size_billion_params"] == 3.0
    assert optimization.settings["quantization"] == "4-bit or 5-bit"


def test_phase11_classical_ml_uses_cpu_without_gpu_dependency():
    report = _low_vram_report(cuda_available=False)
    tools = _tools()
    policy = build_runtime_policy(report, tools)
    capabilities = build_capability_profile(report, tools)
    by_name = {capability.name: capability for capability in capabilities}

    optimization = optimize_for_task("classical_ml", report, tools, policy)

    assert by_name["classical_ml_training"].status == "good"
    assert optimization.task_type == "classical_ml"
    assert optimization.settings["sklearn_available"] is True
    assert optimization.settings["cpu_workflow_allowed"] is True
    assert optimization.settings["gpu_required"] is False
    assert optimization.settings["use_gpu"] is False


def test_task_classifier_routes_common_requests():
    assert classify_task("build a RAG index") == "rag"
    assert classify_task("fix the pytest failure") == "code_assistant"
    assert classify_task("train a sklearn classifier") == "classical_ml"
    assert classify_task("run stable diffusion training") == "image_generation"
