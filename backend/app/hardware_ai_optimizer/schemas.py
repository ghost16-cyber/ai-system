from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class RAMInfo(BaseModel):
    total_mb: int | None = None
    available_mb: int | None = None
    used_mb: int | None = None
    percent_used: float | None = None


class GPUInfo(BaseModel):
    name: str | None = None
    cuda_available: bool = False
    vram_total_mb: int | None = None
    vram_used_mb: int | None = None
    vram_free_mb: int | None = None
    compute_capability: str | None = None
    source: Literal["torch", "nvidia-smi", "none"] = "none"


class StorageInfo(BaseModel):
    path: str
    total_mb: int | None = None
    free_mb: int | None = None


class PyTorchInfo(BaseModel):
    installed: bool = False
    version: str | None = None
    cuda_version: str | None = None


class HardwareReport(BaseModel):
    cpu_name: str
    cpu_count: int
    ram: RAMInfo
    gpu: GPUInfo
    storage: StorageInfo
    pytorch: PyTorchInfo


class RecommendationItem(BaseModel):
    category: str
    priority: Literal["info", "recommended", "warning"]
    message: str
    rationale: str


class RecommendationReport(BaseModel):
    low_vram_mode: bool
    recommended_batch_size_range: list[int] = Field(
        default_factory=lambda: [1, 4],
        min_length=2,
        max_length=2,
    )
    recommended_precision: str
    recommended_models: list[str] = Field(default_factory=list)
    items: list[RecommendationItem] = Field(default_factory=list)


class HardwareOptimizerResponse(BaseModel):
    report: HardwareReport
    recommendations: RecommendationReport
