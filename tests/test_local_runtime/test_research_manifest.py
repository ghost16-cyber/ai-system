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
    get_runtime_research_manifest,
)


def test_runtime_research_manifest_loads_stable_runtime_facts():
    manifest = get_runtime_research_manifest()

    assert manifest.manifest_version == "runtime_research_manifest_v1"
    assert manifest.source_folder == "system information"
    assert manifest.hardware_baseline["gpu"] == "NVIDIA GeForce RTX 3050 Laptop GPU"
    assert manifest.hardware_baseline["vram_gb"] == 4
    assert manifest.policy_defaults["low_vram_mode"] is True
    assert manifest.policy_defaults["prefer_quantized_models"] is True

    fact_ids = {fact.id for fact in manifest.facts}
    assert "fp16_inference_preferred" in fact_ids
    assert "training_requires_small_batches" in fact_ids
    assert "full_finetuning_low_vram_unsafe" in fact_ids
    assert "rag_over_finetuning" in fact_ids
    assert "Live hardware/tool probing remains the source of truth" in manifest.usage_note


def test_live_runtime_policy_is_not_overridden_by_research_manifest_defaults():
    manifest = get_runtime_research_manifest()
    report = HardwareReport(
        cpu_name="High VRAM test workstation",
        cpu_count=24,
        ram=RAMInfo(total_mb=64 * 1024, available_mb=48 * 1024),
        gpu=GPUInfo(
            name="Large GPU",
            cuda_available=True,
            vram_total_mb=16 * 1024,
            vram_free_mb=14 * 1024,
            source="torch",
        ),
        storage=StorageInfo(path="/workspace", total_mb=1024 * 1024, free_mb=800 * 1024),
        pytorch=PyTorchInfo(installed=True, version="2.3.0", cuda_version="12.1"),
    )
    tools = [
        ToolStatus(name="torch", kind="python_package", available="available"),
        ToolStatus(name="faiss", kind="python_package", available="available"),
    ]

    policy = build_runtime_policy(report, tools)

    assert manifest.policy_defaults["low_vram_mode"] is True
    assert policy.low_vram_mode is False
    assert policy.max_recommended_local_model_billion_params != (
        manifest.policy_defaults["max_recommended_local_model_billion_params"]
    )
