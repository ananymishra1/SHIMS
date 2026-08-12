"""Launch-plan computation for the SHIMS native engine.

Turns a hardware profile (from ``shared.neural_governor.hardware_profiler``)
plus a GGUF's header metadata into a concrete koboldcpp launch plan:
backend, GPU layer count, context size, and thread count.

No machine-specific assumptions: everything derives from the profile, the
model file, psutil-available free memory, and env overrides:

- ``SHIMS_NATIVE_GPU_LAYERS`` — force layer offload count (-1 = auto)
- ``SHIMS_NATIVE_CTX``        — force total server context size
- ``SHIMS_NATIVE_THREADS``    — force CPU thread count
- ``SHIMS_NATIVE_BACKEND``    — cuda | vulkan | cpu
- ``SHIMS_NATIVE_PARALLEL``   — parallel request slots (default 4); the
  server context is divided across slots, so total ctx scales with slots
  and slots auto-degrade when the memory budget cannot fund them
"""
from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .discovery import parse_gguf_header

_DEFAULT_CTX = 16384
_MIN_CTX = 2048
_DEFAULT_PARALLEL = 4
_BATCH_SIZE = 512
_HEADROOM = 0.8  # keep 20% of the memory pool free


@dataclass
class LaunchPlan:
    """Concrete koboldcpp launch parameters for one model on this machine."""

    model_path: str
    backend: str               # cuda | vulkan | cpu
    runtime_name: str          # koboldcpp.exe | koboldcpp-nocuda.exe
    gpu_layers: int
    ctx: int                   # total server context; divided across slots
    threads: int
    batch_size: int = _BATCH_SIZE
    parallel_slots: int = 1    # server slots; per-slot ctx = ctx // slots
    block_count: int = 0
    est_weights_bytes: int = 0
    est_kv_bytes: int = 0
    budget_bytes: int = 0
    use_mmap: bool = False     # page weights from disk instead of loading fully into RAM
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def kv_bytes_per_token(block_count: int, embedding_length: int,
                       head_count: int = 0, head_count_kv: int = 0) -> int:
    """Approximate KV-cache bytes per token (K+V, f16).

    GQA-aware: uses ``head_count_kv * (embedding_length / head_count)`` per
    layer when attention head counts are available (Llama-3 style GQA makes
    this ~4x smaller than the naive embedding-based estimate); falls back to
    the embedding-based estimate otherwise.
    """
    embd = embedding_length or 4096
    layers = block_count or 32
    if head_count > 0 and head_count_kv > 0:
        head_dim = embd // head_count
        kv_dim = head_count_kv * head_dim
    else:
        kv_dim = embd
    return layers * kv_dim * 2 * 2


def estimate_kv_bytes(ctx: int, block_count: int, embedding_length: int,
                      head_count: int = 0, head_count_kv: int = 0) -> int:
    return int(ctx) * kv_bytes_per_token(block_count, embedding_length, head_count, head_count_kv)


def _detect_gpu_kind() -> str:
    """nvidia | amd | cpu — reuses the hardware profiler's probes, with a
    minimal Windows registry fallback for modern drivers that only populate
    ``HardwareInformation.AdapterString`` (binary) instead of the legacy
    ``AdapterString`` value the profiler reads (e.g. Ryzen AI MAX / RDNA3.5).
    """
    try:
        from shared.neural_governor import hardware_profiler
        if hardware_profiler._parse_nvidia_smi():
            return "nvidia"
        if hardware_profiler._parse_amd_gpu():
            return "amd"
    except Exception:
        pass
    if os.name == "nt":
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}",
            )
            for i in range(winreg.QueryInfoKey(key)[0]):
                try:
                    subkey = winreg.OpenKey(key, winreg.EnumKey(key, i))
                    for value_name in ("HardwareInformation.AdapterString", "DriverDesc", "AdapterString"):
                        try:
                            raw, _ = winreg.QueryValueEx(subkey, value_name)
                        except Exception:
                            continue
                        if isinstance(raw, bytes):
                            raw = raw.replace(b"\x00", b"").decode("utf-8", "replace")
                        text = str(raw)
                        if "NVIDIA" in text.upper():
                            return "nvidia"
                        if "AMD" in text.upper() or "RADEON" in text.upper():
                            return "amd"
                    winreg.CloseKey(subkey)
                except Exception:
                    continue
        except Exception:
            pass
    return "cpu"


def _available_bytes() -> int:
    import psutil
    return int(psutil.virtual_memory().available)


def _uma_dedicated_bytes() -> int:
    """Dedicated memory of an AMD unified-memory GPU (Windows), e.g. the
    firmware UMA carve-out on Ryzen AI MAX (96 GiB assigned to the iGPU,
    invisible to psutil). Returns 0 when not detectable/applicable."""
    if os.name != "nt":
        return 0
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}",
        )
        best = 0
        for i in range(winreg.QueryInfoKey(key)[0]):
            try:
                subkey = winreg.OpenKey(key, winreg.EnumKey(key, i))
                desc = ""
                for value_name in ("DriverDesc", "HardwareInformation.AdapterString"):
                    try:
                        raw, _ = winreg.QueryValueEx(subkey, value_name)
                    except Exception:
                        continue
                    if isinstance(raw, bytes):
                        raw = raw.replace(b"\x00", b"").decode("utf-8", "replace")
                    desc = desc or str(raw)
                if "AMD" not in desc.upper() and "RADEON" not in desc.upper():
                    winreg.CloseKey(subkey)
                    continue
                try:
                    mem, _ = winreg.QueryValueEx(subkey, "HardwareInformation.qwMemorySize")
                    best = max(best, int(mem))
                except Exception:
                    pass
                winreg.CloseKey(subkey)
            except Exception:
                continue
        return best
    except Exception:
        return 0


def _env_int(name: str) -> int | None:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _cap_ctx_for_budget(ctx: int, block_count: int, embd: int, kv_budget: int,
                        head_count: int = 0, head_count_kv: int = 0) -> int:
    """Largest power-of-two-ish ctx whose KV cache fits ``kv_budget``."""
    per_token = kv_bytes_per_token(block_count, embd, head_count, head_count_kv)
    if per_token <= 0:
        return ctx
    fit = int(kv_budget // per_token)
    capped = min(ctx, fit)
    # Round down to a multiple of 1024 for llama.cpp friendliness.
    capped = (capped // 1024) * 1024
    return max(_MIN_CTX, capped)


def compute_launch_plan(
    hardware_profile: Any,
    gguf_path: str | Path,
    *,
    gpu_kind: str | None = None,
    available_bytes: int | None = None,
) -> LaunchPlan:
    """Compute a launch plan for ``gguf_path`` on the profiled hardware.

    ``hardware_profile`` is a ``HardwareProfile`` (or any object with
    ``total_ram_gb``, ``vram_gb``, ``cpu_cores``). ``gpu_kind`` and
    ``available_bytes`` are injectable for tests; by default the GPU vendor
    is re-probed via the hardware profiler and free RAM comes from psutil.
    """
    path = str(gguf_path)
    try:
        header = parse_gguf_header(path)
    except Exception:
        header = {"arch": "", "name": "", "block_count": 0,
                  "embedding_length": 0, "context_length": 0}
    block_count = int(header.get("block_count") or 0)
    embd = int(header.get("embedding_length") or 0)
    heads = int(header.get("head_count") or 0)
    heads_kv = int(header.get("head_count_kv") or 0)
    weights = int(Path(path).stat().st_size) if Path(path).is_file() else 0
    # Multi-part GGUF: the memory footprint is the SUM of all parts, not just
    # part 1's bytes — otherwise oversized split models look like they fit.
    if re.search(r"-00001-of-\d+$", Path(path).stem, re.IGNORECASE):
        prefix = Path(path).stem.rsplit("-00001-of-", 1)[0]
        try:
            weights = sum(p.stat().st_size for p in Path(path).parent.glob(prefix + "-0*-of-*.gguf"))
        except OSError:
            pass

    kind = (gpu_kind or _detect_gpu_kind()).strip().lower()
    free_ram = int(available_bytes) if available_bytes is not None else _available_bytes()

    # Backend: env override wins, then vendor mapping. Discrete NVIDIA GPUs
    # use CUDA; AMD/unified-memory GPUs use Vulkan (their "VRAM" is system
    # RAM); anything else runs CPU-only with the nocuda runtime.
    backend_env = (os.getenv("SHIMS_NATIVE_BACKEND") or "").strip().lower()
    if backend_env in {"cuda", "vulkan", "cpu"}:
        backend = backend_env
    elif kind == "nvidia":
        backend = "cuda"
    elif kind in {"amd", "unified"}:
        backend = "vulkan"
    else:
        backend = "cpu"
    runtime_name = "koboldcpp-nocuda.exe" if backend == "cpu" else "koboldcpp.exe"

    # Memory pool: discrete GPUs fit layers into VRAM; unified GPUs and CPU
    # share system RAM. Reserve math uses *available* bytes, never totals,
    # with 20% headroom. Exception: AMD UMA firmware carve-outs (Ryzen AI
    # MAX: up to 96 GiB assigned to the iGPU) are a dedicated GPU pool that
    # psutil cannot see — probe the driver and use it when it is larger.
    # Only probed on a real run (available_bytes not injected) so tests stay
    # deterministic.
    vram_bytes = int(float(getattr(hardware_profile, "vram_gb", 0.0) or 0.0) * (1024 ** 3))
    if backend == "cuda" and vram_bytes > 0:
        pool = vram_bytes
    elif backend == "vulkan" and available_bytes is None:
        pool = max(free_ram, _uma_dedicated_bytes())
    else:
        pool = free_ram
    budget = int(pool * _HEADROOM)

    # Parallel request slots (llama.cpp continuous batching via koboldcpp's
    # --parallelrequests). Waves, swarm agents, and background jobs all hit
    # the same engine; with one slot they serialize. The server context is
    # divided across slots, so the total ctx target scales with slots and is
    # anchored to the per-turn context the chat lanes actually send
    # (SHIMS_CHAT_CTX, default 8192).
    slots_env = _env_int("SHIMS_NATIVE_PARALLEL")
    slots = max(1, slots_env if slots_env is not None else _DEFAULT_PARALLEL)
    chat_ctx = _env_int("SHIMS_CHAT_CTX") or 8192

    ctx_env = _env_int("SHIMS_NATIVE_CTX")
    ctx = ctx_env or _DEFAULT_CTX
    if ctx_env is None and slots > 1 and ctx < chat_ctx * slots:
        ctx = chat_ctx * slots
    kv = estimate_kv_bytes(ctx, block_count, embd, heads, heads_kv)

    gpu_layers_env = _env_int("SHIMS_NATIVE_GPU_LAYERS")
    reason = ""
    use_mmap = False
    if weights > pool:
        # Oversized model (e.g. 235B Q4_K_M = 142GB > 133GB RAM): it can never
        # fit any memory pool, so GPU offload is pointless and a full in-RAM
        # load is impossible. Run CPU-only and page weights from disk via mmap
        # (MoE models only touch the active experts per token, so this stays
        # usable — slow but correct), with a single slot and modest ctx.
        backend = "cpu"
        runtime_name = "koboldcpp-nocuda.exe"
        gpu_layers = 0
        use_mmap = True
        slots = 1
        ctx = min(ctx, 8192)
        kv = estimate_kv_bytes(ctx, block_count, embd, heads, heads_kv)
        reason = f"oversized model ({weights // (1024**3)}GB > {pool // (1024**3)}GB pool): cpu-only, mmap paging, single slot"
    elif backend == "cpu":
        gpu_layers = 0
        ctx = _cap_ctx_for_budget(ctx, block_count, embd, budget // 2, heads, heads_kv)
        kv = estimate_kv_bytes(ctx, block_count, embd, heads, heads_kv)
        reason = "cpu-only: no GPU offload, ctx capped to half the free-RAM budget"
    elif weights + kv <= budget:
        gpu_layers = block_count or 999
        reason = "full GPU offload: weights + KV fit the budget with 20% headroom"
    elif weights + estimate_kv_bytes(_MIN_CTX, block_count, embd, heads, heads_kv) <= budget:
        # Full offload is possible if we shrink ctx. ALWAYS prefer that over
        # partial offload: a split CPU/GPU graph on Vulkan/UMA is a measured
        # performance cliff, not a graceful degradation — the 40B Q8 ran at
        # 548 tok/s prompt / 15.7 tok/s gen fully offloaded (ctx 32768) and
        # collapsed to 18.8 / 1.56 tok/s at 94/96 layers with graph splits=24
        # when a larger ctx squeezed out the last two layers. Context is the
        # flexible resource; keeping every layer on the GPU is not negotiable.
        ctx = _cap_ctx_for_budget(ctx, block_count, embd, budget - weights, heads, heads_kv)
        kv = estimate_kv_bytes(ctx, block_count, embd, heads, heads_kv)
        gpu_layers = block_count or 999
        reason = f"full GPU offload with ctx reduced to {ctx}: partial offload is a Vulkan/UMA perf cliff"
    else:
        # Even minimum-ctx KV doesn't fit alongside the weights — genuine
        # partial offload territory.
        ctx = _cap_ctx_for_budget(ctx, block_count, embd, max(budget - min(weights, budget), budget // 4), heads, heads_kv)
        kv = estimate_kv_bytes(ctx, block_count, embd, heads, heads_kv)
        layer_bytes = (weights // block_count) if block_count else weights
        if layer_bytes > 0:
            gpu_layers = max(0, min(block_count, (budget - kv) // layer_bytes))
        else:
            gpu_layers = 0
        reason = f"partial GPU offload: {gpu_layers}/{block_count} layers fit the budget"

    # If ctx was capped below what the requested slots need, shed slots
    # rather than starving every turn of context.
    if slots > 1:
        slots = max(1, min(slots, ctx // max(chat_ctx, _MIN_CTX)))

    if gpu_layers_env is not None and gpu_layers_env >= 0:
        gpu_layers = gpu_layers_env
        reason += " (gpu_layers pinned by SHIMS_NATIVE_GPU_LAYERS)"

    # Threads: physical cores − 2 (min 2). The profiler reports logical
    # cores, so prefer psutil's physical count when available.
    cores = int(getattr(hardware_profile, "cpu_cores", 0) or 0)
    try:
        import psutil
        cores = int(psutil.cpu_count(logical=False) or cores)
    except Exception:
        pass
    threads = _env_int("SHIMS_NATIVE_THREADS") or max(2, cores - 2)

    return LaunchPlan(
        model_path=path,
        backend=backend,
        runtime_name=runtime_name,
        gpu_layers=int(gpu_layers),
        ctx=int(ctx),
        threads=int(threads),
        parallel_slots=int(slots),
        block_count=block_count,
        est_weights_bytes=weights,
        est_kv_bytes=kv,
        budget_bytes=budget,
        use_mmap=use_mmap,
        reason=reason + (f"; {slots} parallel slot(s), {ctx // max(slots, 1)} ctx each" if slots > 1 else ""),
    )
