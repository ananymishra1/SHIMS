"""Oversized-model launch planning: 235B-class models must go CPU+mmap."""
from __future__ import annotations

import shared.native_engine.tuning as tuning


class _Prof:
    total_ram_gb = 128.0
    vram_gb = 8.0
    cpu_cores = 16


def _touch(path, size: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as fh:
        fh.truncate(size)


def test_split_model_weights_are_summed(tmp_path):
    part1 = tmp_path / "Big-00001-of-00003.gguf"
    _touch(part1, 100)
    _touch(tmp_path / "Big-00002-of-00003.gguf", 90)
    _touch(tmp_path / "Big-00003-of-00003.gguf", 80)
    plan = tuning.compute_launch_plan(_Prof(), part1, gpu_kind="amd", available_bytes=200)
    assert plan.est_weights_bytes == 270


def test_oversized_model_forces_cpu_mmap(tmp_path):
    # Weights exceed the free-RAM pool: cannot fit, must page from disk.
    big = tmp_path / "HugeModel.gguf"
    _touch(big, 300 * 1024**2)  # 300MB weights
    plan = tuning.compute_launch_plan(_Prof(), big, gpu_kind="amd", available_bytes=200 * 1024**2)
    assert plan.backend == "cpu"
    assert plan.gpu_layers == 0
    assert plan.use_mmap is True
    assert plan.parallel_slots == 1
    assert plan.ctx <= 8192
    assert plan.runtime_name == "koboldcpp-nocuda.exe"


def test_fitting_model_keeps_gpu(tmp_path):
    small = tmp_path / "SmallModel.gguf"
    _touch(small, 50 * 1024**2)  # 50MB weights on a 1GB-free machine
    plan = tuning.compute_launch_plan(_Prof(), small, gpu_kind="amd", available_bytes=32 * 1024**3)
    assert plan.backend == "vulkan"
    assert plan.use_mmap is False
    assert plan.gpu_layers > 0
