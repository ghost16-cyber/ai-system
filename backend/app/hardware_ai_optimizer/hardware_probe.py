from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

from backend.app.hardware_ai_optimizer.schemas import (
    GPUInfo,
    HardwareReport,
    PyTorchInfo,
    RAMInfo,
    StorageInfo,
)
from backend.app.local_ai.hardware import (
    HardwareCapabilityRegistry,
    HardwareSnapshot,
    collect_hardware_snapshot,
)


def _mb(value: int | float | None) -> int | None:
    return None if value is None else int(round(value / 1024 / 1024))


def snapshot_to_hardware_report(
    snapshot: HardwareSnapshot, *, storage_path: str | Path = "."
) -> HardwareReport:
    total = snapshot.total_memory_bytes
    available = snapshot.available_memory_bytes
    used = max(total - available, 0) if total is not None and available is not None else None
    percent = round((used / total) * 100, 2) if used is not None and total else None
    primary = snapshot.gpu_devices[0] if snapshot.gpu_devices else None
    gpu_total = primary.total_vram_bytes if primary else None
    gpu_free = primary.free_vram_bytes if primary else None
    gpu_used = max(gpu_total - gpu_free, 0) if gpu_total is not None and gpu_free is not None else None
    source = "none"
    if primary is not None:
        source = "nvidia-smi" if "nvidia-smi" in snapshot.probe_sources else "torch"
    return HardwareReport(
        cpu_name=snapshot.cpu_name or "unknown",
        cpu_count=snapshot.cpu_logical_cores or 1,
        ram=RAMInfo(
            total_mb=_mb(total),
            available_mb=_mb(available),
            used_mb=_mb(used),
            percent_used=percent,
        ),
        gpu=GPUInfo(
            name=primary.name if primary else None,
            cuda_available=bool(snapshot.cuda_available),
            vram_total_mb=_mb(gpu_total),
            vram_used_mb=_mb(gpu_used),
            vram_free_mb=_mb(gpu_free),
            compute_capability=primary.compute_capability if primary else None,
            source=source,
        ),
        storage=get_storage_info(storage_path),
        pytorch=PyTorchInfo(
            installed=bool(snapshot.pytorch_installed),
            version=snapshot.pytorch_version,
            cuda_version=snapshot.runtime_cuda_version,
        ),
    )


def get_storage_info(path: str | Path = ".") -> StorageInfo:
    resolved_path = Path(path).expanduser().resolve()
    try:
        usage = shutil.disk_usage(resolved_path)
    except OSError:
        return StorageInfo(path=str(resolved_path))
    return StorageInfo(
        path=str(resolved_path), total_mb=_mb(usage.total), free_mb=_mb(usage.free)
    )


def probe_hardware(
    storage_path: str | Path = ".",
    torch_module: Any | None = None,
    nvidia_smi_runner: Callable[[list[str]], subprocess.CompletedProcess[str]]
    | None = None,
    *,
    registry: HardwareCapabilityRegistry | None = None,
) -> HardwareReport:
    if registry is not None:
        snapshot = registry.snapshot()
    else:
        command_runner = None
        if nvidia_smi_runner is not None:
            command_runner = lambda args, _timeout: nvidia_smi_runner(args)
        snapshot = collect_hardware_snapshot(
            torch_module=torch_module,
            command_runner=command_runner,
            # An injected torch object is a deterministic compatibility probe;
            # do not mix it with the host's unrelated nvidia-smi result.
            nvidia_smi_available=(
                False
                if torch_module is not None and nvidia_smi_runner is None
                else None
            ),
        )
    return snapshot_to_hardware_report(snapshot, storage_path=storage_path)


def get_ram_info(*, registry: HardwareCapabilityRegistry | None = None) -> RAMInfo:
    snapshot = (registry or HardwareCapabilityRegistry()).snapshot()
    return snapshot_to_hardware_report(snapshot).ram


def get_gpu_info(
    torch_module: Any | None = None,
    nvidia_smi_runner: Callable[[list[str]], subprocess.CompletedProcess[str]]
    | None = None,
) -> GPUInfo:
    return probe_hardware(
        torch_module=torch_module, nvidia_smi_runner=nvidia_smi_runner
    ).gpu


def get_pytorch_info(torch_module: Any | None = None) -> PyTorchInfo:
    return probe_hardware(torch_module=torch_module).pytorch


__all__ = [
    "get_gpu_info",
    "get_pytorch_info",
    "get_ram_info",
    "get_storage_info",
    "probe_hardware",
    "snapshot_to_hardware_report",
]
