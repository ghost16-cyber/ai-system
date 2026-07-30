from __future__ import annotations

from pathlib import Path

from backend.app.hardware_ai_optimizer import (
    GPUInfo,
    HardwareReport,
    PyTorchInfo,
    RAMInfo,
    StorageInfo,
    probe_hardware,
    recommend_training_settings,
)


class _FakeProperties:
    total_memory = 4 * 1024 * 1024 * 1024


class _FakeCuda:
    @staticmethod
    def is_available() -> bool:
        return True

    @staticmethod
    def get_device_properties(device_index: int) -> _FakeProperties:
        assert device_index == 0
        return _FakeProperties()

    @staticmethod
    def memory_allocated(device_index: int) -> int:
        assert device_index == 0
        return 512 * 1024 * 1024

    @staticmethod
    def memory_reserved(device_index: int) -> int:
        assert device_index == 0
        return 1024 * 1024 * 1024

    @staticmethod
    def mem_get_info(device_index: int) -> tuple[int, int]:
        assert device_index == 0
        return (3 * 1024 * 1024 * 1024, 4 * 1024 * 1024 * 1024)

    @staticmethod
    def get_device_capability(device_index: int) -> tuple[int, int]:
        assert device_index == 0
        return (8, 6)

    @staticmethod
    def get_device_name(device_index: int) -> str:
        assert device_index == 0
        return "RTX 3050 Laptop GPU"


class _FakeTorchVersion:
    cuda = "12.1"


class _FakeTorch:
    __version__ = "2.3.0"
    cuda = _FakeCuda()
    version = _FakeTorchVersion()


def test_probe_hardware_reads_cuda_gpu_from_torch(tmp_path: Path):
    report = probe_hardware(storage_path=tmp_path, torch_module=_FakeTorch)

    assert report.cpu_count >= 1
    assert report.gpu.name == "RTX 3050 Laptop GPU"
    assert report.gpu.cuda_available is True
    assert report.gpu.vram_total_mb == 4096
    assert report.gpu.vram_used_mb == 1024
    assert report.gpu.vram_free_mb == 3072
    assert report.gpu.compute_capability == "8.6"
    assert report.gpu.source == "torch"
    assert report.pytorch.installed is True
    assert report.pytorch.version == "2.3.0"
    assert report.pytorch.cuda_version == "12.1"
    assert report.storage.path == str(tmp_path.resolve())


def test_recommendations_enable_low_vram_profile_for_four_gb_gpu():
    report = HardwareReport(
        cpu_name="Intel i5",
        cpu_count=12,
        ram=RAMInfo(total_mb=32 * 1024, available_mb=20 * 1024),
        gpu=GPUInfo(
            name="RTX 3050 Laptop GPU",
            cuda_available=True,
            vram_total_mb=4096,
            vram_free_mb=3072,
            source="torch",
        ),
        storage=StorageInfo(path="/workspace", total_mb=512 * 1024, free_mb=100 * 1024),
        pytorch=PyTorchInfo(installed=True, version="2.3.0", cuda_version="12.1"),
    )

    recommendations = recommend_training_settings(report)

    assert recommendations.low_vram_mode is True
    assert recommendations.recommended_batch_size_range == [2, 4]
    assert recommendations.recommended_precision == "mixed_precision"
    assert "MobileNetV2" in recommendations.recommended_models
    assert any(item.category == "batching" for item in recommendations.items)


def test_recommendations_warn_when_cuda_is_missing():
    report = HardwareReport(
        cpu_name="Intel i5",
        cpu_count=12,
        ram=RAMInfo(total_mb=32 * 1024, available_mb=20 * 1024),
        gpu=GPUInfo(cuda_available=False),
        storage=StorageInfo(path="/workspace", total_mb=512 * 1024, free_mb=100 * 1024),
        pytorch=PyTorchInfo(installed=False),
    )

    recommendations = recommend_training_settings(report)

    assert recommendations.low_vram_mode is True
    assert recommendations.recommended_precision == "fp32_cpu"
    assert recommendations.recommended_batch_size_range == [1, 2]
    assert recommendations.items[0].priority == "warning"
    assert "CUDA" in recommendations.items[0].message
