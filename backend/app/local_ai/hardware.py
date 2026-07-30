from __future__ import annotations

import importlib
import os
import platform
import subprocess
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, NamedTuple

from pydantic import Field

from backend.app.project_control.contracts import StrictModel


_MEMINFO_LIMIT_BYTES = 64 * 1024
_CPUINFO_LIMIT_BYTES = 2 * 1024 * 1024
CommandRunner = Callable[[list[str], float], subprocess.CompletedProcess[str]]


class MemoryProbeResult(NamedTuple):
    total_bytes: int | None
    available_bytes: int | None
    estimate: bool
    provenance: dict[str, object]


class GPUDeviceSnapshot(StrictModel):
    index: int = Field(ge=0)
    stable_identity: str | None = None
    name: str | None = None
    total_vram_bytes: int | None = Field(default=None, ge=0)
    free_vram_bytes: int | None = Field(default=None, ge=0)
    utilization_percent: float | None = Field(default=None, ge=0, le=100)
    compute_capability: str | None = None


class HardwareSnapshot(StrictModel):
    schema_version: Literal["astra.hardware.snapshot.v1"] = (
        "astra.hardware.snapshot.v1"
    )
    collected_at: datetime
    platform: str
    operating_system: str
    architecture: str
    cpu_name: str | None = None
    cpu_logical_cores: int | None = Field(default=None, ge=1)
    cpu_physical_cores: int | None = Field(default=None, ge=1)
    total_memory_bytes: int | None = Field(default=None, ge=0)
    available_memory_bytes: int | None = Field(default=None, ge=0)
    available_memory_estimate: bool = False
    memory_provenance: dict[str, object] = Field(default_factory=dict)
    gpu_devices: tuple[GPUDeviceSnapshot, ...] = ()
    cuda_available: bool | None = None
    driver_version: str | None = None
    runtime_cuda_version: str | None = None
    pytorch_installed: bool | None = None
    pytorch_version: str | None = None
    probe_sources: tuple[str, ...] = ()
    partial: bool = False
    errors: tuple[str, ...] = ()

    @property
    def total_vram_bytes(self) -> int | None:
        values = [item.total_vram_bytes for item in self.gpu_devices]
        return sum(value for value in values if value is not None) if any(
            value is not None for value in values
        ) else None

    @property
    def free_vram_bytes(self) -> int | None:
        values = [item.free_vram_bytes for item in self.gpu_devices]
        return sum(value for value in values if value is not None) if any(
            value is not None for value in values
        ) else None


def parse_linux_meminfo(text: str) -> MemoryProbeResult:
    values: dict[str, int] = {}
    for raw_line in text.splitlines():
        key, separator, raw_value = raw_line.partition(":")
        if not separator:
            continue
        parts = raw_value.split()
        if not parts:
            continue
        try:
            value = max(0, int(parts[0]))
        except ValueError:
            continue
        multiplier = 1024 if len(parts) > 1 and parts[1].lower() == "kb" else 1
        values[key.strip()] = value * multiplier
    total = values.get("MemTotal")
    available = values.get("MemAvailable")
    if available is not None:
        return MemoryProbeResult(
            total,
            min(available, total) if total is not None else available,
            False,
            {"probe": "proc_meminfo", "available_source": "MemAvailable"},
        )
    reclaimable_cache = max(
        0,
        values.get("Cached", 0)
        + values.get("SReclaimable", 0)
        - values.get("Shmem", 0),
    )
    fallback_parts = ("MemFree", "Buffers", "Cached", "SReclaimable")
    estimated = None
    if any(name in values for name in fallback_parts):
        estimated = values.get("MemFree", 0) + values.get("Buffers", 0) + reclaimable_cache
        if total is not None:
            estimated = min(estimated, total)
    return MemoryProbeResult(
        total,
        estimated,
        True,
        {
            "probe": "proc_meminfo",
            "available_source": "MemFree+Buffers+Cached+SReclaimable-Shmem",
            "estimate": True,
        },
    )


def probe_host_memory(
    *, system: str | None = None, meminfo_path: Path = Path("/proc/meminfo")
) -> MemoryProbeResult:
    if (system or platform.system()).lower() == "linux":
        try:
            payload = _bounded_read(meminfo_path, _MEMINFO_LIMIT_BYTES)
            result = parse_linux_meminfo(payload.decode("ascii", errors="replace"))
            if result.total_bytes is not None:
                return result
        except (OSError, ValueError):
            pass
    total = available = None
    try:
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
        total = int(os.sysconf("SC_PHYS_PAGES")) * page_size
        available = int(os.sysconf("SC_AVPHYS_PAGES")) * page_size
    except (AttributeError, OSError, TypeError, ValueError):
        pass
    return MemoryProbeResult(
        total,
        available,
        True,
        {
            "probe": "stdlib_sysconf",
            "available_source": "SC_AVPHYS_PAGES",
            "estimate": True,
        },
    )


def collect_hardware_snapshot(
    *,
    system: str | None = None,
    meminfo_path: Path = Path("/proc/meminfo"),
    cpuinfo_path: Path = Path("/proc/cpuinfo"),
    torch_module: Any | None = None,
    command_runner: CommandRunner | None = None,
    nvidia_smi_available: bool | None = None,
    collected_at: datetime | None = None,
) -> HardwareSnapshot:
    os_name = system or platform.system()
    errors: list[str] = []
    sources: list[str] = []
    memory = probe_host_memory(system=os_name, meminfo_path=meminfo_path)
    sources.append(str(memory.provenance["probe"]))
    cpu_name, physical_cores = _cpu_details(os_name, cpuinfo_path)
    logical_cores = os.cpu_count() or None
    torch = torch_module
    if torch is None:
        try:
            torch = importlib.import_module("torch")
        except Exception:
            torch = None
    pytorch_installed = torch is not None
    pytorch_version = str(getattr(torch, "__version__", "")) or None if torch else None
    version_module = getattr(torch, "version", None) if torch else None
    runtime_cuda = getattr(version_module, "cuda", None)
    cuda_available: bool | None = None
    if torch is not None:
        try:
            cuda_available = bool(torch.cuda.is_available())
            sources.append("torch")
        except Exception:
            errors.append("torch_cuda_probe_failed")
    runner = command_runner or _run_command
    if nvidia_smi_available is None:
        import shutil

        nvidia_smi_available = shutil.which("nvidia-smi") is not None
    devices: tuple[GPUDeviceSnapshot, ...] = ()
    driver_version = None
    if nvidia_smi_available or command_runner is not None:
        devices, driver_version, smi_error = _probe_nvidia_smi(runner)
        if smi_error:
            errors.append(smi_error)
        else:
            sources.append("nvidia-smi")
            cuda_available = bool(devices)
    if not devices and torch is not None and cuda_available:
        try:
            devices = _devices_from_torch(torch)
        except Exception:
            errors.append("torch_gpu_probe_failed")
    if not nvidia_smi_available and command_runner is None:
        errors.append("nvidia_smi_unavailable")
    return HardwareSnapshot(
        collected_at=collected_at or datetime.now(timezone.utc),
        platform=platform.platform(),
        operating_system=os_name,
        architecture=platform.machine() or "unknown",
        cpu_name=cpu_name,
        cpu_logical_cores=logical_cores,
        cpu_physical_cores=physical_cores,
        total_memory_bytes=memory.total_bytes,
        available_memory_bytes=memory.available_bytes,
        available_memory_estimate=memory.estimate,
        memory_provenance=memory.provenance,
        gpu_devices=devices,
        cuda_available=cuda_available,
        driver_version=driver_version,
        runtime_cuda_version=str(runtime_cuda) if runtime_cuda else None,
        pytorch_installed=pytorch_installed,
        pytorch_version=pytorch_version,
        probe_sources=tuple(dict.fromkeys(sources)),
        partial=bool(errors) or memory.total_bytes is None,
        errors=tuple(dict.fromkeys(errors)),
    )


class HardwareCapabilityRegistry:
    """Short-lived advisory snapshots; never owns scheduler state or claims."""

    def __init__(
        self,
        probe: Callable[[], HardwareSnapshot] = collect_hardware_snapshot,
        *,
        ttl_seconds: float = 5.0,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if not 0 <= ttl_seconds <= 60:
            raise ValueError("hardware snapshot TTL must be between 0 and 60 seconds")
        self._probe = probe
        self._ttl_seconds = ttl_seconds
        self._monotonic = monotonic
        self._cached: HardwareSnapshot | None = None
        self._cached_at: float | None = None

    def snapshot(self, *, refresh: bool = False) -> HardwareSnapshot:
        now = self._monotonic()
        if (
            not refresh
            and self._cached is not None
            and self._cached_at is not None
            and now - self._cached_at < self._ttl_seconds
        ):
            return self._cached
        snapshot = self._probe()
        self._cached = snapshot
        self._cached_at = now
        return snapshot


def _probe_nvidia_smi(
    runner: CommandRunner,
) -> tuple[tuple[GPUDeviceSnapshot, ...], str | None, str | None]:
    command = [
        "nvidia-smi",
        "--query-gpu=index,uuid,name,memory.total,memory.used,memory.free,utilization.gpu,driver_version",
        "--format=csv,noheader,nounits",
    ]
    try:
        completed = runner(command, 2.0)
    except subprocess.TimeoutExpired:
        return (), None, "nvidia_smi_timeout"
    except (OSError, subprocess.SubprocessError):
        return (), None, "nvidia_smi_unavailable"
    except Exception:
        return (), None, "nvidia_smi_probe_failed"
    if completed.returncode != 0:
        return (), None, "nvidia_smi_failed"
    devices: list[GPUDeviceSnapshot] = []
    driver = None
    try:
        for line in completed.stdout.splitlines():
            if not line.strip():
                continue
            parts = [part.strip() for part in line.split(",")]
            if len(parts) != 8:
                raise ValueError
            index, identity, name, total, _used, free, utilization, row_driver = parts
            devices.append(
                GPUDeviceSnapshot(
                    index=int(index),
                    stable_identity=identity or None,
                    name=name or None,
                    total_vram_bytes=int(total) * 1024 * 1024,
                    free_vram_bytes=int(free) * 1024 * 1024,
                    utilization_percent=float(utilization),
                )
            )
            driver = driver or row_driver or None
    except (ValueError, TypeError):
        return (), None, "nvidia_smi_malformed"
    return tuple(devices), driver, None


def _devices_from_torch(torch: Any) -> tuple[GPUDeviceSnapshot, ...]:
    devices: list[GPUDeviceSnapshot] = []
    device_count = int(torch.cuda.device_count()) if hasattr(torch.cuda, "device_count") else 1
    for index in range(device_count):
        properties = torch.cuda.get_device_properties(index)
        free = None
        if hasattr(torch.cuda, "mem_get_info"):
            free = int(torch.cuda.mem_get_info(index)[0])
        capability = None
        if hasattr(torch.cuda, "get_device_capability"):
            major, minor = torch.cuda.get_device_capability(index)
            capability = f"{major}.{minor}"
        devices.append(
            GPUDeviceSnapshot(
                index=index,
                stable_identity=f"torch:{index}:{torch.cuda.get_device_name(index)}",
                name=str(torch.cuda.get_device_name(index)),
                total_vram_bytes=int(properties.total_memory),
                free_vram_bytes=free,
                compute_capability=capability,
            )
        )
    return tuple(devices)


def _cpu_details(system: str, cpuinfo_path: Path) -> tuple[str | None, int | None]:
    if system.lower() == "linux":
        try:
            text = _bounded_read(cpuinfo_path, _CPUINFO_LIMIT_BYTES).decode(
                "utf-8", errors="replace"
            )
            name = None
            pairs: set[tuple[str, str]] = set()
            physical = core = None
            for line in (*text.splitlines(), ""):
                if not line.strip():
                    if physical is not None and core is not None:
                        pairs.add((physical, core))
                    physical = core = None
                    continue
                key, separator, value = line.partition(":")
                if not separator:
                    continue
                key = key.strip().lower()
                value = value.strip()
                if key == "model name" and not name:
                    name = value or None
                elif key == "physical id":
                    physical = value
                elif key == "core id":
                    core = value
            return name or platform.processor() or None, len(pairs) or None
        except (OSError, ValueError):
            pass
    return platform.processor() or platform.machine() or None, None


def _run_command(args: list[str], timeout_seconds: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        capture_output=True,
        check=False,
        encoding="utf-8",
        timeout=timeout_seconds,
        shell=False,
    )


def _bounded_read(path: Path, limit: int) -> bytes:
    with path.open("rb") as handle:
        payload = handle.read(limit + 1)
    if len(payload) > limit:
        raise ValueError("hardware_probe_input_too_large")
    return payload


__all__ = [
    "CommandRunner",
    "GPUDeviceSnapshot",
    "HardwareCapabilityRegistry",
    "HardwareSnapshot",
    "MemoryProbeResult",
    "collect_hardware_snapshot",
    "parse_linux_meminfo",
    "probe_host_memory",
]
