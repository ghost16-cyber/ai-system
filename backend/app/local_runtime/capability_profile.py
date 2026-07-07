from __future__ import annotations

from backend.app.hardware_ai_optimizer.schemas import HardwareReport
from backend.app.local_runtime.schemas import Capability, RuntimePolicy, ToolStatus


def tool_available(tools: list[ToolStatus], name: str) -> bool:
    return any(tool.name == name and tool.available == "available" for tool in tools)


def build_runtime_policy(report: HardwareReport, tools: list[ToolStatus]) -> RuntimePolicy:
    vram_total = report.gpu.vram_total_mb or 0
    ram_total = report.ram.total_mb or 0
    low_vram_mode = not report.gpu.cuda_available or vram_total <= 6 * 1024

    notes: list[str] = []
    if not report.gpu.cuda_available:
        notes.append("CUDA is not visible; prefer CPU-safe workflows.")
    elif vram_total <= 4 * 1024:
        notes.append("4GB-class VRAM detected; avoid large local models and large batches.")
    if ram_total >= 24 * 1024:
        notes.append("System RAM is strong enough for RAG, small models, and CPU fallback.")
    if tool_available(tools, "ollama"):
        notes.append("Ollama is available for local quantized model serving.")

    max_params = 3.0 if low_vram_mode else 7.0
    if not report.gpu.cuda_available and ram_total < 16 * 1024:
        max_params = 1.5

    return RuntimePolicy(
        low_vram_mode=low_vram_mode,
        prefer_quantized_models=True,
        avoid_large_models=low_vram_mode,
        prefer_rag_over_finetuning=low_vram_mode,
        prefer_cpu_fallback=not report.gpu.cuda_available or low_vram_mode,
        cpu_fallback_allowed=True,
        max_recommended_local_model_billion_params=max_params,
        notes=notes,
    )


def build_capability_profile(
    report: HardwareReport,
    tools: list[ToolStatus],
) -> list[Capability]:
    policy = build_runtime_policy(report, tools)
    has_torch = tool_available(tools, "torch")
    has_sklearn = tool_available(tools, "sklearn")
    has_ollama = tool_available(tools, "ollama")
    has_transformers = tool_available(tools, "transformers")
    has_git = tool_available(tools, "git")

    capabilities = [
        Capability(
            name="local_slm_inference",
            status="limited" if has_ollama or has_transformers else "unavailable",
            reason=(
                "Use small quantized models; the laptop profile is not suited for large local LLMs."
                if has_ollama or has_transformers
                else "No local model runner was detected."
            ),
            recommended_tools=["ollama", "llama.cpp", "transformers"],
            limits=["prefer 1.5B-3B quantized models", "avoid large context windows"]
            if policy.low_vram_mode
            else ["prefer quantized models for speed"],
        ),
        Capability(
            name="code_assistant_loop",
            status="good" if has_git else "limited",
            reason=(
                "Python and Git are available for local analysis, tests, and patch workflows."
                if has_git
                else "Python is available, but Git was not detected for repository-aware safety."
            ),
            recommended_tools=["python", "pytest", "git"],
            limits=[],
        ),
        Capability(
            name="pytorch_cuda_training",
            status=_pytorch_training_status(report, has_torch),
            reason=_pytorch_training_reason(report, has_torch),
            recommended_tools=["torch", "cuda", "nvidia-smi"],
            limits=_training_limits(report),
        ),
        Capability(
            name="classical_ml_training",
            status="good" if has_sklearn else "limited",
            reason=(
                "scikit-learn is available and fits the CPU/RAM profile well."
                if has_sklearn
                else "Classical ML is feasible, but scikit-learn was not detected."
            ),
            recommended_tools=["sklearn", "numpy", "joblib"],
            limits=["prefer CPU-friendly baselines before neural fine-tuning"],
        ),
        Capability(
            name="rag_workflows",
            status="good" if has_transformers or has_ollama else "limited",
            reason="RAG is a strong fit for low-VRAM hardware because it avoids full model training.",
            recommended_tools=["sentence_transformers", "faiss", "ollama"],
            limits=["use compact embedding models", "cache indexes on SSD"],
        ),
        Capability(
            name="large_model_finetuning",
            status="unavailable" if policy.low_vram_mode else "limited",
            reason=(
                "The detected VRAM profile is too small for large LLM fine-tuning."
                if policy.low_vram_mode
                else "Possible only with careful parameter-efficient tuning."
            ),
            recommended_tools=["cloud_gpu", "qlora"],
            limits=["use cloud GPU for heavy experiments", "avoid full fine-tuning locally"],
        ),
    ]
    return capabilities


def _pytorch_training_status(report: HardwareReport, has_torch: bool) -> str:
    if not has_torch:
        return "unavailable"
    if not report.gpu.cuda_available:
        return "limited"
    if report.gpu.vram_total_mb is not None and report.gpu.vram_total_mb <= 4 * 1024:
        return "limited"
    return "good"


def _pytorch_training_reason(report: HardwareReport, has_torch: bool) -> str:
    if not has_torch:
        return "PyTorch was not detected."
    if not report.gpu.cuda_available:
        return "PyTorch is available, but CUDA is not visible."
    if report.gpu.vram_total_mb is not None and report.gpu.vram_total_mb <= 4 * 1024:
        return "CUDA is available, but VRAM is in the 4GB low-memory range."
    return "PyTorch and CUDA are available with enough VRAM for moderate local training."


def _training_limits(report: HardwareReport) -> list[str]:
    if not report.gpu.cuda_available:
        return ["CPU training only", "use small datasets and classical baselines"]
    if report.gpu.vram_total_mb is not None and report.gpu.vram_total_mb <= 4 * 1024:
        return [
            "batch size 2-4",
            "mixed precision",
            "gradient accumulation",
            "freeze backbones",
            "avoid large transformers",
        ]
    return ["use automatic batch probing", "monitor VRAM during dry runs"]
