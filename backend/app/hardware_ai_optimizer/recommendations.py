from __future__ import annotations

from backend.app.hardware_ai_optimizer.schemas import (
    HardwareReport,
    RecommendationItem,
    RecommendationReport,
)


def _item(
    category: str,
    priority: str,
    message: str,
    rationale: str,
) -> RecommendationItem:
    return RecommendationItem(
        category=category,
        priority=priority,  # type: ignore[arg-type]
        message=message,
        rationale=rationale,
    )


def recommend_training_settings(report: HardwareReport) -> RecommendationReport:
    vram_total = report.gpu.vram_total_mb
    ram_total = report.ram.total_mb
    storage_free = report.storage.free_mb
    cuda_available = report.gpu.cuda_available

    items: list[RecommendationItem] = []

    if not cuda_available:
        items.append(
            _item(
                "runtime",
                "warning",
                "CUDA is not currently available.",
                "Training should stay CPU-first until NVIDIA drivers, WSL CUDA, and PyTorch CUDA are visible.",
            )
        )
        return RecommendationReport(
            low_vram_mode=True,
            recommended_batch_size_range=[1, 2],
            recommended_precision="fp32_cpu",
            recommended_models=["LogisticRegression", "small CNN", "MobileNetV2"],
            items=items,
        )

    if vram_total is None:
        low_vram_mode = True
        batch_range = [1, 4]
        precision = "mixed_precision"
        items.append(
            _item(
                "vram",
                "warning",
                "VRAM size could not be detected.",
                "Astra should assume a conservative low-VRAM profile until the GPU reports memory capacity.",
            )
        )
    elif vram_total <= 4096:
        low_vram_mode = True
        batch_range = [2, 4]
        precision = "mixed_precision"
        items.extend(
            [
                _item(
                    "vram",
                    "recommended",
                    "Enable low-VRAM mode.",
                    "4GB-class GPUs are useful for transfer learning and small models, but batch size must stay conservative.",
                ),
                _item(
                    "batching",
                    "recommended",
                    "Start with batch size 2-4 and increase only after a dry run.",
                    "Small batches reduce CUDA out-of-memory risk on laptop RTX 3050-style GPUs.",
                ),
            ]
        )
    elif vram_total <= 8192:
        low_vram_mode = True
        batch_range = [4, 8]
        precision = "mixed_precision"
        items.append(
            _item(
                "vram",
                "recommended",
                "Use a moderate low-VRAM profile.",
                "6-8GB GPUs can handle more models, but mixed precision and careful batching still help.",
            )
        )
    else:
        low_vram_mode = False
        batch_range = [8, 32]
        precision = "mixed_precision"
        items.append(
            _item(
                "vram",
                "info",
                "Standard GPU training settings are reasonable.",
                "VRAM is above the low-memory laptop range, though automatic batch probing is still useful.",
            )
        )

    items.extend(
        [
            _item(
                "precision",
                "recommended",
                "Use automatic mixed precision for CUDA training.",
                "AMP usually lowers VRAM use while preserving training speed on NVIDIA GPUs.",
            ),
            _item(
                "training",
                "recommended",
                "Prefer transfer learning, frozen backbones, and gradient accumulation.",
                "These techniques stretch small GPUs without pretending they can train large models from scratch.",
            ),
            _item(
                "model_choice",
                "recommended",
                "Prefer ResNet18, MobileNetV2, EfficientNet-B0, or small YOLO experiments first.",
                "These model families are realistic starting points for a laptop GPU with limited VRAM.",
            ),
        ]
    )

    if ram_total is not None and ram_total < 16 * 1024:
        items.append(
            _item(
                "ram",
                "warning",
                "System RAM is below the preferred 16GB floor.",
                "Large datasets, workers, and CPU offloading can become unstable with limited RAM.",
            )
        )

    if storage_free is not None and storage_free < 20 * 1024:
        items.append(
            _item(
                "storage",
                "warning",
                "Free storage is below 20GB.",
                "Datasets, checkpoints, caches, and virtual environments can fill the disk quickly.",
            )
        )

    return RecommendationReport(
        low_vram_mode=low_vram_mode,
        recommended_batch_size_range=batch_range,
        recommended_precision=precision,
        recommended_models=[
            "ResNet18",
            "MobileNetV2",
            "EfficientNet-B0",
            "small YOLO",
            "small text classifier",
        ],
        items=items,
    )
