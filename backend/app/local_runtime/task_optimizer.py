from __future__ import annotations

from backend.app.hardware_ai_optimizer.schemas import HardwareReport
from backend.app.local_runtime.capability_profile import tool_available
from backend.app.local_runtime.schemas import RuntimePolicy, TaskOptimization, ToolStatus


def optimize_for_task(
    task: str | None,
    report: HardwareReport,
    tools: list[ToolStatus],
    policy: RuntimePolicy,
) -> TaskOptimization:
    task_type = classify_task(task or "")
    if task_type == "pytorch_training":
        return _optimize_pytorch_training(report, tools, policy)
    if task_type == "local_slm":
        return _optimize_local_slm(report, tools, policy)
    if task_type == "rag":
        return _optimize_rag(tools, policy)
    if task_type == "classical_ml":
        return _optimize_classical_ml(tools)
    if task_type == "image_generation":
        return _optimize_image_generation(policy)
    if task_type == "code_assistant":
        return _optimize_code_assistant(tools, policy)
    return _optimize_unknown(policy)


def classify_task(task: str) -> str:
    lowered = task.lower()
    if any(token in lowered for token in ("rag", "retrieval", "embedding", "vector")):
        return "rag"
    if any(token in lowered for token in ("ollama", "slm", "llm", "chatbot", "local model")):
        return "local_slm"
    if any(token in lowered for token in ("stable diffusion", "image generation", "diffusion")):
        return "image_generation"
    if any(
        token in lowered
        for token in (
            "classical_ml",
            "classical ml",
            "classifier",
            "sklearn",
            "regression",
            "random forest",
        )
    ):
        return "classical_ml"
    if any(token in lowered for token in ("pytorch", "train", "fine-tune", "finetune", "batch size")):
        return "pytorch_training"
    if any(token in lowered for token in ("fix", "test", "pytest", "code", "patch", "repo")):
        return "code_assistant"
    return "unknown"


def _optimize_pytorch_training(
    report: HardwareReport,
    tools: list[ToolStatus],
    policy: RuntimePolicy,
) -> TaskOptimization:
    has_torch = tool_available(tools, "torch")
    warnings: list[str] = []
    if not has_torch:
        warnings.append("PyTorch is not detected; install a CUDA-compatible PyTorch build first.")
    if not report.gpu.cuda_available:
        warnings.append("CUDA is not visible; use CPU baselines or fix CUDA before GPU training.")
    elif policy.low_vram_mode:
        warnings.append("Low VRAM detected; avoid large transformers and large image sizes.")

    return TaskOptimization(
        task_type="pytorch_training",
        local_fit="limited" if policy.low_vram_mode else "good",
        suggested_runtime="PyTorch with CUDA low-VRAM settings" if report.gpu.cuda_available else "CPU-first PyTorch or scikit-learn",
        recommended_tools=["torch", "nvidia-smi", "psutil"],
        settings={
            "batch_size": [2, 4] if policy.low_vram_mode else [8, 32],
            "cuda_available": report.gpu.cuda_available,
            "mixed_precision": bool(report.gpu.cuda_available),
            "gradient_accumulation": policy.low_vram_mode,
            "freeze_backbone": policy.low_vram_mode,
            "preferred_models": ["MobileNetV2", "ResNet18", "EfficientNet-B0"],
            "large_model_training_discouraged": policy.avoid_large_models,
            "avoid": ["large LLM fine-tuning", "large image generation training"],
        },
        warnings=warnings,
        next_steps=[
            "Run a tiny dry-run batch before full training.",
            "Measure VRAM during the first forward/backward pass.",
            "Lower batch size or image size after any CUDA out-of-memory error.",
        ],
    )


def _optimize_local_slm(
    report: HardwareReport,
    tools: list[ToolStatus],
    policy: RuntimePolicy,
) -> TaskOptimization:
    has_ollama = tool_available(tools, "ollama")
    warnings = []
    if not has_ollama:
        warnings.append("Ollama is not detected; use the configured SLM provider or install a local runner.")
    if policy.low_vram_mode:
        warnings.append("Use quantized small models and keep context windows conservative.")

    return TaskOptimization(
        task_type="local_slm",
        local_fit="limited" if policy.low_vram_mode else "good",
        suggested_runtime="Ollama or llama.cpp with quantized small SLM",
        recommended_tools=["ollama", "llama.cpp"],
        settings={
            "max_model_size_billion_params": policy.max_recommended_local_model_billion_params,
            "prefer_quantized": policy.prefer_quantized_models,
            "avoid_large_models": policy.avoid_large_models,
            "cpu_fallback_allowed": policy.cpu_fallback_allowed,
            "quantization": "4-bit or 5-bit",
            "prefer_models": ["1.5B coder model", "3B instruct/coder model"],
            "gpu_layers": "auto/conservative" if report.gpu.cuda_available else 0,
            "context_window": "small to moderate",
        },
        warnings=warnings,
        next_steps=[
            "Prefer RAG and tool use over asking the SLM to memorize project context.",
            "Keep prompts compact and include runtime constraints.",
        ],
    )


def _optimize_rag(tools: list[ToolStatus], policy: RuntimePolicy) -> TaskOptimization:
    warnings = []
    if not tool_available(tools, "sentence_transformers"):
        warnings.append("sentence-transformers is not detected; embeddings may need setup.")
    if not tool_available(tools, "faiss"):
        warnings.append("FAISS is not detected; use a simple index first or install FAISS later.")

    return TaskOptimization(
        task_type="rag",
        local_fit="good",
        suggested_runtime="CPU/GPU hybrid RAG with cached embeddings",
        recommended_tools=["sentence_transformers", "faiss", "ollama"],
        settings={
            "embedding_model": "small embedding model",
            "embedding_workflow_recommended": True,
            "faiss_available": tool_available(tools, "faiss"),
            "index_cache": True,
            "fine_tuning_discouraged": policy.prefer_rag_over_finetuning,
            "prefer_rag": True,
            "prefer_rag_over_finetuning": policy.prefer_rag_over_finetuning,
        },
        warnings=warnings,
        next_steps=[
            "Build a small repository/document index.",
            "Cache embeddings on SSD.",
            "Use retrieved snippets as compact SLM context.",
        ],
    )


def _optimize_classical_ml(tools: list[ToolStatus]) -> TaskOptimization:
    sklearn_available = tool_available(tools, "sklearn")
    return TaskOptimization(
        task_type="classical_ml",
        local_fit="good",
        suggested_runtime="CPU-first scikit-learn baseline",
        recommended_tools=["sklearn", "numpy", "joblib"],
        settings={
            "sklearn_available": sklearn_available,
            "cpu_workflow_allowed": True,
            "gpu_required": False,
            "use_gpu": False,
            "start_with_baseline": True,
        },
        warnings=[] if sklearn_available else ["scikit-learn is not detected."],
        next_steps=["Build a classical baseline before using neural training."],
    )


def _optimize_image_generation(policy: RuntimePolicy) -> TaskOptimization:
    return TaskOptimization(
        task_type="image_generation",
        local_fit="poor" if policy.low_vram_mode else "limited",
        suggested_runtime="Use inference only or a cloud GPU for training.",
        recommended_tools=["cloud_gpu", "diffusers"],
        settings={"local_training": False, "prefer_inference_only": True},
        warnings=["Image generation training is not a good fit for low-VRAM laptops."],
        next_steps=["Use cloud GPU for training or keep experiments to tiny inference demos."],
    )


def _optimize_code_assistant(
    tools: list[ToolStatus],
    policy: RuntimePolicy,
) -> TaskOptimization:
    return TaskOptimization(
        task_type="code_assistant",
        local_fit="good",
        suggested_runtime="Astra tool loop with compact SLM context",
        recommended_tools=["python", "pytest", "git", "ollama"],
        settings={
            "include_runtime_context_in_prompt": True,
            "prefer_tools_over_large_prompts": True,
            "prefer_small_local_model": policy.prefer_quantized_models,
        },
        warnings=[] if tool_available(tools, "git") else ["Git is not detected; patch safety is reduced."],
        next_steps=[
            "Use tests and static tools as truth.",
            "Keep SLM prompts compact and grounded in tool results.",
        ],
    )


def _optimize_unknown(policy: RuntimePolicy) -> TaskOptimization:
    return TaskOptimization(
        task_type="unknown",
        local_fit="unknown",
        suggested_runtime="Inspect task requirements before choosing runtime.",
        recommended_tools=["python", "git", "pytest"],
        settings={
            "low_vram_mode": policy.low_vram_mode,
            "prefer_quantized_models": policy.prefer_quantized_models,
            "avoid_large_models": policy.avoid_large_models,
            "cpu_fallback_allowed": policy.cpu_fallback_allowed,
            "prefer_rag_over_finetuning": policy.prefer_rag_over_finetuning,
        },
        warnings=["Task type is unclear; avoid committing to heavy GPU work yet."],
        next_steps=["Classify the task, then select tools and settings."],
    )
