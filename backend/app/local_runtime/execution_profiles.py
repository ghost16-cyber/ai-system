from __future__ import annotations

from typing import Any

from backend.app.local_runtime.schemas import ExecutionProfile, RuntimeContext
from backend.app.local_runtime.task_optimizer import classify_task


def build_execution_profile(
    task: str,
    runtime_context: RuntimeContext | dict[str, Any],
    active_runtime_plan: dict[str, Any],
) -> ExecutionProfile:
    if not isinstance(active_runtime_plan, dict) or not active_runtime_plan:
        raise ValueError("An active validated runtime plan is required.")

    context = _context_dict(runtime_context)
    policy = _dict_value(context, "policy")
    hardware = _dict_value(context, "hardware")
    task_optimization = _dict_value(context, "task_optimization")
    tools = _tool_availability(context)
    task_type = _effective_task_type(task, active_runtime_plan)
    strategy = str(active_runtime_plan.get("strategy") or task_type)

    if task_type == "local_slm":
        return _local_slm_profile(
            strategy,
            active_runtime_plan,
            policy,
            hardware,
            tools,
        )
    if task_type == "pytorch_training":
        return _pytorch_profile(
            strategy,
            active_runtime_plan,
            policy,
            hardware,
            task_optimization,
        )
    if task_type == "rag":
        return _rag_profile(
            strategy,
            active_runtime_plan,
            policy,
            tools,
        )
    if task_type == "classical_ml":
        return _classical_ml_profile(
            strategy,
            active_runtime_plan,
            tools,
        )
    raise ValueError(f"No execution profile is available for task type: {task_type}")


def _local_slm_profile(
    strategy: str,
    plan: dict[str, Any],
    policy: dict[str, Any],
    hardware: dict[str, Any],
    tools: dict[str, bool],
) -> ExecutionProfile:
    cuda_available = _cuda_available(hardware)
    low_vram = bool(policy.get("low_vram_mode"))
    quantized = bool(
        plan.get("use_quantized_model", policy.get("prefer_quantized_models", True))
    )
    runtime = "unavailable"
    for candidate in ("ollama", "llama_cpp", "transformers"):
        if tools.get(candidate):
            runtime = candidate
            break
    device = "cuda" if cuda_available else "cpu"
    return ExecutionProfile(
        task_type="local_slm",
        strategy=strategy,
        runtime=runtime,
        device=device,
        settings={
            "model_size_limit_billion_params": policy.get(
                "max_recommended_local_model_billion_params"
            ),
            "quantization_required": quantized,
            "quantization": "4-bit or 5-bit" if quantized else "optional",
            "max_context_tokens": 4096 if low_vram else 8192,
            "cpu_fallback_allowed": bool(policy.get("cpu_fallback_allowed")),
            "timeout_seconds": 180 if device == "cpu" else 120,
            "max_parallel_requests": 1 if low_vram else 2,
        },
        required_tools=[runtime] if runtime != "unavailable" else ["ollama"],
        optional_tools=["nvidia-smi", "psutil"],
        safeguards=[
            "reject models above the configured size limit",
            "keep context within the profile maximum",
            "monitor RAM and VRAM before model load",
            "install a supported local model runtime before execution"
            if runtime == "unavailable"
            else "use only the selected installed runtime",
        ],
        source_plan=plan,
    )


def _pytorch_profile(
    strategy: str,
    plan: dict[str, Any],
    policy: dict[str, Any],
    hardware: dict[str, Any],
    task_optimization: dict[str, Any],
) -> ExecutionProfile:
    cuda_available = _cuda_available(hardware)
    requested_device = str(plan.get("device") or "").lower()
    device = "cuda" if cuda_available and requested_device != "cpu" else "cpu"
    low_vram = bool(policy.get("low_vram_mode"))
    optimization_settings = _dict_value(task_optimization, "settings")
    batch_range = optimization_settings.get(
        "batch_size",
        [2, 4] if low_vram else [8, 32],
    )
    return ExecutionProfile(
        task_type="pytorch_training",
        strategy=strategy,
        runtime="pytorch",
        device=device,
        settings={
            "batch_size_range": batch_range,
            "initial_batch_size": batch_range[0],
            "gradient_accumulation": low_vram,
            "gradient_accumulation_steps": 4 if low_vram else 1,
            "mixed_precision": device == "cuda",
            "checkpoint_every_steps": 250 if low_vram else 500,
            "pin_memory": device == "cuda",
            "memory_monitoring": True,
            "cpu_fallback_allowed": bool(policy.get("cpu_fallback_allowed")),
        },
        required_tools=["torch"],
        optional_tools=["nvidia-smi", "psutil"],
        safeguards=[
            "run a one-batch dry run before training",
            "reduce batch size after CUDA out-of-memory",
            "save checkpoints before long training intervals",
        ],
        source_plan=plan,
    )


def _rag_profile(
    strategy: str,
    plan: dict[str, Any],
    policy: dict[str, Any],
    tools: dict[str, bool],
) -> ExecutionProfile:
    faiss_available = tools.get("faiss", False)
    embedding_backend = "unavailable"
    for candidate in ("sentence_transformers", "transformers"):
        if tools.get(candidate):
            embedding_backend = candidate
            break
    return ExecutionProfile(
        task_type="rag",
        strategy=strategy,
        runtime="rag_pipeline",
        device="hybrid",
        settings={
            "embedding_backend": embedding_backend,
            "vector_backend": "faiss" if faiss_available else "numpy",
            "faiss_enabled": faiss_available,
            "chunk_size_tokens": 512,
            "chunk_overlap_tokens": 64,
            "top_k": 5,
            "reranking_allowed": not bool(policy.get("low_vram_mode")),
            "cache_embeddings": True,
            "fine_tuning_enabled": False,
        },
        required_tools=(
            [embedding_backend]
            if embedding_backend != "unavailable"
            else ["sentence_transformers"]
        ),
        optional_tools=["faiss", "ollama"],
        safeguards=[
            "cache embeddings on disk",
            "limit retrieved context before SLM prompting",
            "fall back to a simple vector index when FAISS is unavailable",
        ],
        source_plan=plan,
    )


def _classical_ml_profile(
    strategy: str,
    plan: dict[str, Any],
    tools: dict[str, bool],
) -> ExecutionProfile:
    return ExecutionProfile(
        task_type="classical_ml",
        strategy=strategy,
        runtime="scikit_learn",
        device="cpu",
        settings={
            "sklearn_pipeline_allowed": tools.get("sklearn", False),
            "gpu_required": False,
            "parallel_jobs": 4,
            "joblib_persistence_allowed": tools.get("joblib", False),
            "cross_validation_folds": 5,
        },
        required_tools=["sklearn"],
        optional_tools=["joblib", "numpy"],
        safeguards=[
            "fit preprocessing inside the pipeline",
            "persist only validated estimators",
            "cap parallel jobs for laptop thermals",
        ],
        source_plan=plan,
    )


def _effective_task_type(task: str, plan: dict[str, Any]) -> str:
    strategy = str(plan.get("strategy") or "").strip().lower().replace("-", "_")
    if strategy in {"rag", "embedding_retrieval", "retrieval"}:
        return "rag"
    if strategy in {"quantized_inference", "local_inference", "slm_inference"}:
        return "local_slm"
    if strategy in {"sklearn_pipeline", "classical_ml"}:
        return "classical_ml"
    if strategy in {
        "small_model_training",
        "full_finetuning",
        "fine_tuning",
        "pytorch_training",
    }:
        return "pytorch_training"
    return classify_task(task)


def _context_dict(runtime_context: RuntimeContext | dict[str, Any]) -> dict[str, Any]:
    if hasattr(runtime_context, "model_dump"):
        return runtime_context.model_dump(mode="json")
    return runtime_context


def _dict_value(value: dict[str, Any], key: str) -> dict[str, Any]:
    item = value.get(key)
    return item if isinstance(item, dict) else {}


def _tool_availability(context: dict[str, Any]) -> dict[str, bool]:
    tools = context.get("tools")
    if isinstance(tools, list):
        return {
            str(tool.get("name")): tool.get("available") == "available"
            for tool in tools
            if isinstance(tool, dict) and tool.get("name")
        }
    availability = context.get("tool_availability")
    if isinstance(availability, dict):
        return {
            str(name): value in {True, "available"}
            for name, value in availability.items()
        }
    return {}


def _cuda_available(hardware: dict[str, Any]) -> bool:
    gpu = hardware.get("gpu")
    return bool(gpu.get("cuda_available")) if isinstance(gpu, dict) else False
