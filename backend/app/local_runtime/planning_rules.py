from __future__ import annotations

from copy import deepcopy
from typing import Any

from backend.app.local_runtime.schemas import PlanValidationResult, RuntimeContext
from backend.app.local_runtime.task_optimizer import classify_task


FULL_FINETUNING_STRATEGIES = {
    "full_finetuning",
    "full_fine_tuning",
    "full-finetuning",
    "full fine tuning",
    "full fine-tuning",
}
FINETUNING_STRATEGIES = FULL_FINETUNING_STRATEGIES | {
    "fine_tuning",
    "finetuning",
    "fine-tuning",
    "fine tuning",
}
LOCAL_INFERENCE_TASKS = {"local_slm", "local_model_inference", "llm_inference"}


def validate_task_plan(
    task: str,
    requested_plan: dict[str, Any],
    runtime_context: RuntimeContext | dict[str, Any],
) -> PlanValidationResult:
    context = _context_dict(runtime_context)
    policy = _policy(context)
    hardware = _hardware(context)
    task_type = classify_task(task)
    if task_type == "unknown":
        task_type = _normalized(str(requested_plan.get("task_type") or task))

    requested = deepcopy(requested_plan)
    recommended = deepcopy(requested_plan)
    blocked_signals: list[str] = []
    reasons: list[str] = []
    hard_block = False

    strategy = _normalized(str(requested_plan.get("strategy") or ""))
    low_vram_mode = bool(policy.get("low_vram_mode"))
    avoid_large_models = bool(policy.get("avoid_large_models"))
    prefer_rag = bool(policy.get("prefer_rag_over_finetuning"))
    cpu_fallback_allowed = bool(
        policy.get("cpu_fallback_allowed", policy.get("prefer_cpu_fallback", False))
    )
    cuda_available = bool(hardware.get("gpu", {}).get("cuda_available"))

    if low_vram_mode and strategy in FULL_FINETUNING_STRATEGIES:
        _append_unique(blocked_signals, "full_finetuning")
        _append_unique(blocked_signals, "large_model_training")
        reasons.append("Full fine-tuning is not suitable for the low-VRAM runtime.")
        recommended.update(
            {
                "strategy": "rag" if prefer_rag else "small_model_training",
                "use_quantized_model": True,
                "allow_cpu_fallback": cpu_fallback_allowed,
                "full_finetuning": False,
            }
        )

    if task_type == "rag" and strategy in FINETUNING_STRATEGIES:
        _append_unique(blocked_signals, "finetuning_first_for_rag")
        reasons.append("RAG tasks should use embeddings and retrieval before fine-tuning.")
        recommended.update(
            {
                "strategy": "embedding_retrieval",
                "embedding_workflow": True,
                "use_fine_tuning": False,
            }
        )

    if avoid_large_models and _requests_large_local_model(
        task_type,
        requested_plan,
        policy,
    ):
        _append_unique(blocked_signals, "large_local_model")
        reasons.append("The requested local model exceeds the safe runtime size.")
        recommended.update(
            {
                "strategy": "quantized_inference",
                "use_quantized_model": True,
                "model_size_billion_params": policy.get(
                    "max_recommended_local_model_billion_params"
                ),
                "allow_cpu_fallback": cpu_fallback_allowed,
            }
        )

    if _requires_gpu(requested_plan) and not cuda_available:
        _append_unique(blocked_signals, "cuda_unavailable")
        if cpu_fallback_allowed:
            reasons.append("CUDA is unavailable; the plan was redirected to CPU.")
            recommended.update(
                {
                    "requires_gpu": False,
                    "use_gpu": False,
                    "device": "cpu",
                    "allow_cpu_fallback": True,
                }
            )
        else:
            hard_block = True
            reasons.append("CUDA is unavailable and this plan has no permitted CPU fallback.")

    if not blocked_signals:
        return PlanValidationResult(
            allowed=True,
            decision="allow",
            reason="The requested plan is compatible with the current runtime policy.",
            requested_plan=requested,
            recommended_plan=recommended,
            blocked_signals=[],
        )

    return PlanValidationResult(
        allowed=False,
        decision="block" if hard_block else "downgrade",
        reason=" ".join(reasons),
        requested_plan=requested,
        recommended_plan={} if hard_block else recommended,
        blocked_signals=blocked_signals,
    )


def _context_dict(runtime_context: RuntimeContext | dict[str, Any]) -> dict[str, Any]:
    if isinstance(runtime_context, RuntimeContext):
        return runtime_context.model_dump(mode="json")
    if hasattr(runtime_context, "model_dump"):
        return runtime_context.model_dump(mode="json")
    return runtime_context


def _policy(context: dict[str, Any]) -> dict[str, Any]:
    policy = context.get("policy")
    if isinstance(policy, dict):
        return policy
    slm_context = context.get("slm_context")
    if isinstance(slm_context, dict):
        runtime_policy = slm_context.get("runtime_policy")
        if isinstance(runtime_policy, dict):
            return runtime_policy
    runtime_policy = context.get("runtime_policy")
    return runtime_policy if isinstance(runtime_policy, dict) else {}


def _hardware(context: dict[str, Any]) -> dict[str, Any]:
    hardware = context.get("hardware")
    if isinstance(hardware, dict):
        return hardware
    slm_context = context.get("slm_context")
    if isinstance(slm_context, dict):
        machine = slm_context.get("machine_summary")
        if isinstance(machine, dict):
            return {
                "gpu": {
                    "cuda_available": machine.get("cuda_available"),
                    "vram_total_mb": machine.get("vram_total_mb"),
                }
            }
    return {}


def _requests_large_local_model(
    task_type: str,
    plan: dict[str, Any],
    policy: dict[str, Any],
) -> bool:
    local_inference = task_type in LOCAL_INFERENCE_TASKS or _normalized(
        str(plan.get("execution") or "")
    ) in {"local", "local_inference"}
    if not local_inference:
        return False

    size_label = _normalized(str(plan.get("model_size") or ""))
    if size_label in {"large", "xlarge", "xl", "huge"}:
        return True

    requested_size = _model_size_billions(plan)
    maximum = policy.get("max_recommended_local_model_billion_params")
    return (
        requested_size is not None
        and isinstance(maximum, (int, float))
        and requested_size > float(maximum)
    )


def _model_size_billions(plan: dict[str, Any]) -> float | None:
    for key in (
        "model_size_billion_params",
        "model_size_b",
        "parameter_count_billion",
    ):
        value = plan.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return None


def _requires_gpu(plan: dict[str, Any]) -> bool:
    if plan.get("requires_gpu") is True or plan.get("use_gpu") is True:
        return True
    return _normalized(str(plan.get("device") or "")) in {"gpu", "cuda"}


def _normalized(value: str) -> str:
    return " ".join(value.strip().lower().replace("-", "_").split())


def _append_unique(items: list[str], value: str) -> None:
    if value not in items:
        items.append(value)
