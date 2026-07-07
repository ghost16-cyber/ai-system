from __future__ import annotations

import ctypes
import importlib
import os
import platform
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


def _mb(value: float | int) -> int:
    return int(round(value / 1024 / 1024))


def _load_optional_module(name: str) -> Any | None:
    try:
        return importlib.import_module(name)
    except Exception:
        return None


def _cpu_name() -> str:
    if platform.system() == "Linux":
        cpuinfo = Path("/proc/cpuinfo")
        if cpuinfo.exists():
            for line in cpuinfo.read_text(errors="ignore").splitlines():
                if line.lower().startswith("model name"):
                    _, _, value = line.partition(":")
                    if value.strip():
                        return value.strip()

    processor = platform.processor().strip()
    if processor:
        return processor

    return platform.machine() or "unknown"


def _ram_from_psutil() -> RAMInfo | None:
    psutil = _load_optional_module("psutil")
    if psutil is None:
        return None

    memory = psutil.virtual_memory()
    return RAMInfo(
        total_mb=_mb(memory.total),
        available_mb=_mb(memory.available),
        used_mb=_mb(memory.used),
        percent_used=round(float(memory.percent), 2),
    )


def _ram_from_sysconf() -> RAMInfo | None:
    if not hasattr(os, "sysconf"):
        return None

    try:
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
        total_pages = int(os.sysconf("SC_PHYS_PAGES"))
        available_pages = int(os.sysconf("SC_AVPHYS_PAGES"))
    except (OSError, ValueError):
        return None

    total = page_size * total_pages
    available = page_size * available_pages
    used = max(total - available, 0)
    percent_used = round((used / total) * 100, 2) if total else None
    return RAMInfo(
        total_mb=_mb(total),
        available_mb=_mb(available),
        used_mb=_mb(used),
        percent_used=percent_used,
    )


def _ram_from_windows_api() -> RAMInfo | None:
    if platform.system() != "Windows":
        return None

    class MemoryStatus(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    status = MemoryStatus()
    status.dwLength = ctypes.sizeof(MemoryStatus)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return None

    used = max(status.ullTotalPhys - status.ullAvailPhys, 0)
    return RAMInfo(
        total_mb=_mb(status.ullTotalPhys),
        available_mb=_mb(status.ullAvailPhys),
        used_mb=_mb(used),
        percent_used=round(float(status.dwMemoryLoad), 2),
    )


def get_ram_info() -> RAMInfo:
    return (
        _ram_from_psutil()
        or _ram_from_sysconf()
        or _ram_from_windows_api()
        or RAMInfo()
    )


def get_storage_info(path: str | Path = ".") -> StorageInfo:
    resolved_path = Path(path).expanduser().resolve()
    try:
        usage = shutil.disk_usage(resolved_path)
    except OSError:
        return StorageInfo(path=str(resolved_path))

    return StorageInfo(
        path=str(resolved_path),
        total_mb=_mb(usage.total),
        free_mb=_mb(usage.free),
    )


def get_pytorch_info(torch_module: Any | None = None) -> PyTorchInfo:
    torch = torch_module if torch_module is not None else _load_optional_module("torch")
    if torch is None:
        return PyTorchInfo()

    version_module = getattr(torch, "version", None)
    return PyTorchInfo(
        installed=True,
        version=str(getattr(torch, "__version__", "")) or None,
        cuda_version=getattr(version_module, "cuda", None),
    )


def _gpu_from_torch(torch_module: Any | None = None) -> GPUInfo | None:
    torch = torch_module if torch_module is not None else _load_optional_module("torch")
    if torch is None:
        return None

    cuda = getattr(torch, "cuda", None)
    if cuda is None or not cuda.is_available():
        return GPUInfo(cuda_available=False, source="torch")

    device_index = 0
    properties = cuda.get_device_properties(device_index)
    vram_total_mb = _mb(properties.total_memory)
    allocated_mb = _mb(cuda.memory_allocated(device_index))
    reserved_mb = _mb(cuda.memory_reserved(device_index))
    vram_used_mb = max(allocated_mb, reserved_mb)
    vram_free_mb = None

    if hasattr(cuda, "mem_get_info"):
        free_bytes, _total_bytes = cuda.mem_get_info(device_index)
        vram_free_mb = _mb(free_bytes)
    elif vram_total_mb is not None:
        vram_free_mb = max(vram_total_mb - vram_used_mb, 0)

    capability = None
    if hasattr(cuda, "get_device_capability"):
        major, minor = cuda.get_device_capability(device_index)
        capability = f"{major}.{minor}"

    return GPUInfo(
        name=str(cuda.get_device_name(device_index)),
        cuda_available=True,
        vram_total_mb=vram_total_mb,
        vram_used_mb=vram_used_mb,
        vram_free_mb=vram_free_mb,
        compute_capability=capability,
        source="torch",
    )


def _gpu_from_nvidia_smi(
    runner: Callable[[list[str]], subprocess.CompletedProcess[str]] | None = None,
) -> GPUInfo | None:
    if shutil.which("nvidia-smi") is None and runner is None:
        return None

    command = [
        "nvidia-smi",
        "--query-gpu=name,memory.total,memory.used,memory.free",
        "--format=csv,noheader,nounits",
    ]
    run = runner or (
        lambda args: subprocess.run(
            args,
            capture_output=True,
            check=False,
            encoding="utf-8",
            timeout=2,
        )
    )

    try:
        completed = run(command)
    except Exception:
        return None

    if completed.returncode != 0:
        return None

    first_line = completed.stdout.splitlines()[0] if completed.stdout else ""
    parts = [part.strip() for part in first_line.split(",")]
    if len(parts) != 4:
        return None

    name, total, used, free = parts
    try:
        return GPUInfo(
            name=name,
            cuda_available=True,
            vram_total_mb=int(total),
            vram_used_mb=int(used),
            vram_free_mb=int(free),
            source="nvidia-smi",
        )
    except ValueError:
        return None


def get_gpu_info(
    torch_module: Any | None = None,
    nvidia_smi_runner: Callable[[list[str]], subprocess.CompletedProcess[str]]
    | None = None,
) -> GPUInfo:
    torch_gpu = _gpu_from_torch(torch_module)
    if torch_gpu is not None and torch_gpu.cuda_available:
        return torch_gpu

    smi_gpu = _gpu_from_nvidia_smi(nvidia_smi_runner)
    if smi_gpu is not None:
        return smi_gpu

    return torch_gpu or GPUInfo()


def probe_hardware(
    storage_path: str | Path = ".",
    torch_module: Any | None = None,
    nvidia_smi_runner: Callable[[list[str]], subprocess.CompletedProcess[str]]
    | None = None,
) -> HardwareReport:
    return HardwareReport(
        cpu_name=_cpu_name(),
        cpu_count=os.cpu_count() or 1,
        ram=get_ram_info(),
        gpu=get_gpu_info(
            torch_module=torch_module,
            nvidia_smi_runner=nvidia_smi_runner,
        ),
        storage=get_storage_info(storage_path),
        pytorch=get_pytorch_info(torch_module),
    )
