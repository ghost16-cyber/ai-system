from __future__ import annotations

from pathlib import Path

from backend.app.hardware_ai_optimizer import probe_hardware
from backend.app.local_ai.hardware import HardwareCapabilityRegistry
from backend.app.local_runtime.capability_profile import (
    build_capability_profile,
    build_runtime_policy,
)
from backend.app.local_runtime.schemas import RuntimeContext
from backend.app.local_runtime.task_optimizer import optimize_for_task
from backend.app.local_runtime.tool_detector import detect_toolchain


def build_runtime_context(
    task: str | None = None,
    workspace_root: str | Path = ".",
    *,
    hardware_registry: HardwareCapabilityRegistry | None = None,
) -> RuntimeContext:
    hardware = probe_hardware(workspace_root, registry=hardware_registry)
    tools = detect_toolchain()
    policy = build_runtime_policy(hardware, tools)
    capabilities = build_capability_profile(hardware, tools)
    task_optimization = optimize_for_task(task, hardware, tools, policy)

    return RuntimeContext(
        hardware=hardware,
        tools=tools,
        capabilities=capabilities,
        policy=policy,
        task_optimization=task_optimization,
        slm_context=_build_slm_context(
            hardware=hardware,
            policy=policy,
            task_optimization=task_optimization,
            capabilities=capabilities,
        ),
    )


def _build_slm_context(
    *,
    hardware,
    policy,
    task_optimization,
    capabilities,
) -> dict:
    capability_summary = {
        capability.name: capability.status
        for capability in capabilities
    }
    return {
        "machine_summary": {
            "cpu": hardware.cpu_name,
            "ram_total_mb": hardware.ram.total_mb,
            "gpu": hardware.gpu.name,
            "cuda_available": hardware.gpu.cuda_available,
            "vram_total_mb": hardware.gpu.vram_total_mb,
            "pytorch_installed": hardware.pytorch.installed,
        },
        "runtime_policy": {
            "low_vram_mode": policy.low_vram_mode,
            "prefer_quantized_models": policy.prefer_quantized_models,
            "avoid_large_models": policy.avoid_large_models,
            "prefer_rag_over_finetuning": policy.prefer_rag_over_finetuning,
            "cpu_fallback_allowed": policy.cpu_fallback_allowed,
            "max_recommended_local_model_billion_params": (
                policy.max_recommended_local_model_billion_params
            ),
        },
        "capabilities": capability_summary,
        "task_optimization": task_optimization.model_dump(mode="json"),
        "planning_rules": [
            "Check local runtime limits before selecting AI models or training settings.",
            "Prefer small quantized local models on low-VRAM machines.",
            "Prefer RAG, tools, and compact context over large fine-tuning jobs.",
            "Use CPU-safe fallbacks when CUDA or VRAM is insufficient.",
            "Run dry checks before long GPU jobs.",
        ],
    }
