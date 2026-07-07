from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


ResearchFactStatus = Literal["confirmed", "recommended", "warning"]


class RuntimeResearchFact(BaseModel):
    id: str
    label: str
    status: ResearchFactStatus
    summary: str
    evidence: str


class RuntimeResearchManifest(BaseModel):
    manifest_version: str = "runtime_research_manifest_v1"
    source_folder: str = "system information"
    hardware_baseline: dict[str, str | int | float | bool] = Field(default_factory=dict)
    facts: list[RuntimeResearchFact] = Field(default_factory=list)
    policy_defaults: dict[str, bool | int | float | str] = Field(default_factory=dict)
    usage_note: str


def get_runtime_research_manifest() -> RuntimeResearchManifest:
    return RuntimeResearchManifest(
        hardware_baseline={
            "gpu": "NVIDIA GeForce RTX 3050 Laptop GPU",
            "vram_gb": 4,
            "cuda_available": True,
            "pytorch_cuda_available": True,
            "cpu_threads": 12,
        },
        facts=[
            RuntimeResearchFact(
                id="rtx3050_compute_capable",
                label="RTX 3050 CUDA compute is useful",
                status="confirmed",
                summary=(
                    "Repeated tensor workloads can benefit from CUDA when data "
                    "stays on the GPU."
                ),
                evidence=(
                    "PyTorch CUDA benchmark reports showed strong speedups for "
                    "batched and repeated matrix/neural-network workloads."
                ),
            ),
            RuntimeResearchFact(
                id="vram_is_primary_limit",
                label="4 GB VRAM is the main local constraint",
                status="warning",
                summary=(
                    "The GPU has useful compute capability, but model size, "
                    "context size, and batch size must stay conservative."
                ),
                evidence=(
                    "Runtime reports identify the RTX 3050 Laptop GPU as "
                    "compute-capable but VRAM-limited."
                ),
            ),
            RuntimeResearchFact(
                id="fp16_inference_preferred",
                label="FP16 inference is preferred",
                status="recommended",
                summary=(
                    "Inference should prefer CUDA, FP16, and inference mode "
                    "when numerically acceptable."
                ),
                evidence=(
                    "The status report recommends CUDA + FP16 for inference and "
                    "records low VRAM use in inference runs."
                ),
            ),
            RuntimeResearchFact(
                id="training_requires_small_batches",
                label="Training needs small-batch safeguards",
                status="recommended",
                summary=(
                    "Training should start with small batches, gradient-aware "
                    "settings, checkpoints, and VRAM monitoring."
                ),
                evidence=(
                    "Execution analysis found training much slower and heavier "
                    "than inference in the measured workload history."
                ),
            ),
            RuntimeResearchFact(
                id="rag_over_finetuning",
                label="Prefer RAG before fine-tuning",
                status="recommended",
                summary=(
                    "Knowledge adaptation should start with retrieval and compact "
                    "context before attempting fine-tuning."
                ),
                evidence=(
                    "Runtime planning reports recommend RAG, tools, and compact "
                    "context over large local fine-tuning jobs."
                ),
            ),
            RuntimeResearchFact(
                id="full_finetuning_low_vram_unsafe",
                label="Full fine-tuning is unsafe on low VRAM",
                status="warning",
                summary=(
                    "Full fine-tuning of large local models should be blocked or "
                    "downgraded on this laptop-class runtime."
                ),
                evidence=(
                    "The continuation report describes a closed-loop runtime "
                    "control layer with profile gates and VRAM pressure gates."
                ),
            ),
        ],
        policy_defaults={
            "low_vram_mode": True,
            "prefer_quantized_models": True,
            "avoid_large_models": True,
            "prefer_rag_over_finetuning": True,
            "cpu_fallback_allowed": True,
            "max_recommended_local_model_billion_params": 3.0,
        },
        usage_note=(
            "This manifest is explanatory baseline evidence from local research "
            "reports. Live hardware/tool probing remains the source of truth for "
            "runtime policy decisions."
        ),
    )
