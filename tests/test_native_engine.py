"""Tests for the SHIMS native engine (Phase 4).

Covers: GGUF header parsing (synthetic + the real 8B file when present),
launch-plan tuning math on synthetic hardware profiles, discovery, full
lifecycle against a fake koboldcpp runtime, and provider registration.
"""
from __future__ import annotations

import socket
import struct
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from shared.native_engine import discovery, tuning
from shared.native_engine.tuning import LaunchPlan, compute_launch_plan

REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_GGUF = REPO_ROOT / "storage" / "models" / "Llama-3.1-8B-Instruct-Q4_K_M.gguf"
GB = 1024 ** 3


@pytest.fixture(autouse=True)
def _clean_native_env(monkeypatch):
    for var in ("SHIMS_NATIVE_GPU_LAYERS", "SHIMS_NATIVE_CTX", "SHIMS_NATIVE_THREADS",
                "SHIMS_NATIVE_BACKEND", "SHIMS_NATIVE_MODEL", "SHIMS_NATIVE_EXTRA_MODEL_DIRS"):
        monkeypatch.delenv(var, raising=False)


def _write_fake_gguf(path: Path, total_size: int = 4096, arch: str = "llama",
                     block_count: int = 32, embd: int = 4096, heads: int = 32,
                     heads_kv: int = 8) -> Path:
    """Write a minimal valid GGUF header, padded to ``total_size``."""
    kvs: list[tuple[str, object]] = [
        ("general.architecture", arch),
        ("general.name", "Fake Model"),
        (f"{arch}.block_count", block_count),
        (f"{arch}.embedding_length", embd),
        (f"{arch}.context_length", 131072),
        (f"{arch}.attention.head_count", heads),
        (f"{arch}.attention.head_count_kv", heads_kv),
    ]
    buf = bytearray()
    buf += b"GGUF" + struct.pack("<IQQ", 3, 0, len(kvs))
    for key, value in kvs:
        kb = key.encode()
        buf += struct.pack("<Q", len(kb)) + kb
        if isinstance(value, str):
            vb = value.encode()
            buf += struct.pack("<I", 8) + struct.pack("<Q", len(vb)) + vb
        else:
            buf += struct.pack("<I", 4) + struct.pack("<i", int(value))
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(bytes(buf))
        # Extend without zero-filling gigabytes (instant on NTFS).
        fh.seek(total_size - 1)
        fh.write(b"\0")
    return path


def _hw(ram_gb: float, vram_gb: float, cores: int) -> SimpleNamespace:
    return SimpleNamespace(total_ram_gb=ram_gb, vram_gb=vram_gb, cpu_cores=cores)


# --------------------------------------------------------------------- #
# GGUF header parser
# --------------------------------------------------------------------- #

def test_parse_fake_gguf_header(tmp_path):
    path = _write_fake_gguf(tmp_path / "fake.gguf")
    header = discovery.parse_gguf_header(path)
    assert header["arch"] == "llama"
    assert header["name"] == "Fake Model"
    assert header["block_count"] == 32
    assert header["embedding_length"] == 4096
    assert header["head_count"] == 32
    assert header["head_count_kv"] == 8


@pytest.mark.skipif(not REAL_GGUF.is_file(), reason="real 8B GGUF not present")
def test_parse_real_gguf_header():
    header = discovery.parse_gguf_header(REAL_GGUF)
    assert header["arch"] == "llama"
    assert header["block_count"] > 0


# --------------------------------------------------------------------- #
# Tuning math
# --------------------------------------------------------------------- #

def test_tuning_unified_amd_full_offload(tmp_path, monkeypatch):
    """128 GB unified AMD APU: everything fits — all layers on GPU. With the
    default 4 parallel slots the ctx target scales to 4x the per-turn chat
    ctx (4 x 8192 = 32768) since the server context is divided across slots."""
    # User-saved Brain Controls values in .env must not leak into the defaults test.
    for var in ("SHIMS_CHAT_CTX", "SHIMS_NATIVE_CTX", "SHIMS_NATIVE_PARALLEL",
                "SHIMS_NATIVE_GPU_LAYERS", "SHIMS_NATIVE_BACKEND", "SHIMS_NATIVE_THREADS"):
        monkeypatch.delenv(var, raising=False)
    gguf = _write_fake_gguf(tmp_path / "m.gguf", total_size=int(4.9 * GB))
    plan = compute_launch_plan(_hw(128, 128, 32), gguf, gpu_kind="amd",
                               available_bytes=100 * GB)
    assert plan.backend == "vulkan"
    assert plan.runtime_name == "koboldcpp.exe"
    assert plan.gpu_layers == 32
    assert plan.ctx == 32768
    assert plan.parallel_slots == 4
    assert plan.threads >= 2


def test_tuning_nvidia_discrete_cuda(tmp_path):
    """16 GB VRAM NVIDIA + 64 GB RAM: CUDA backend, weights+KV fit VRAM budget."""
    gguf = _write_fake_gguf(tmp_path / "m.gguf", total_size=int(4.9 * GB))
    plan = compute_launch_plan(_hw(64, 16, 16), gguf, gpu_kind="nvidia",
                               available_bytes=48 * GB)
    assert plan.backend == "cuda"
    assert plan.runtime_name == "koboldcpp.exe"
    assert plan.gpu_layers == 32  # 4.9 GB weights + ~2.1 GB KV < 12.8 GB budget


def test_tuning_nvidia_small_vram_partial(tmp_path):
    """6 GB VRAM NVIDIA: only a fraction of the layers fits the budget."""
    gguf = _write_fake_gguf(tmp_path / "m.gguf", total_size=int(4.9 * GB))
    plan = compute_launch_plan(_hw(64, 6, 16), gguf, gpu_kind="nvidia",
                               available_bytes=48 * GB)
    assert plan.backend == "cuda"
    assert 0 <= plan.gpu_layers < 32
    assert plan.ctx >= 2048


def test_tuning_cpu_only(tmp_path):
    """32 GB RAM, no GPU: no offload, nocuda runtime."""
    gguf = _write_fake_gguf(tmp_path / "m.gguf", total_size=int(4.9 * GB))
    plan = compute_launch_plan(_hw(32, 0, 8), gguf, gpu_kind="cpu",
                               available_bytes=24 * GB)
    assert plan.backend == "cpu"
    assert plan.runtime_name == "koboldcpp-nocuda.exe"
    assert plan.gpu_layers == 0
    assert plan.ctx >= 2048


def test_tuning_env_overrides_win(tmp_path, monkeypatch):
    monkeypatch.setenv("SHIMS_NATIVE_GPU_LAYERS", "7")
    monkeypatch.setenv("SHIMS_NATIVE_CTX", "4096")
    monkeypatch.setenv("SHIMS_NATIVE_THREADS", "5")
    monkeypatch.setenv("SHIMS_NATIVE_BACKEND", "cpu")
    gguf = _write_fake_gguf(tmp_path / "m.gguf", total_size=int(4.9 * GB))
    plan = compute_launch_plan(_hw(128, 128, 32), gguf, gpu_kind="amd",
                               available_bytes=100 * GB)
    assert plan.backend == "cpu"
    assert plan.gpu_layers == 7
    assert plan.ctx == 4096
    assert plan.threads == 5


# --------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------- #

def test_discovery_finds_ggufs(tmp_path):
    storage = tmp_path / "storage" / "models"
    nested = tmp_path / "lmstudio" / "publisher" / "repo"
    _write_fake_gguf(storage / "alpha.gguf")
    _write_fake_gguf(nested / "beta.gguf")
    models = discovery.discover_models(dirs=[storage, tmp_path / "lmstudio"])
    by_id = {m["id"]: m for m in models}
    assert set(by_id) == {"alpha", "beta"}
    assert by_id["alpha"]["arch"] == "llama"
    assert by_id["alpha"]["block_count"] == 32
    assert by_id["alpha"]["size_bytes"] > 0


def test_pick_default_model_prefers_storage(tmp_path):
    storage = tmp_path / "storage" / "models"
    other = tmp_path / "other"
    _write_fake_gguf(storage / "small.gguf", total_size=4096)
    _write_fake_gguf(other / "huge.gguf", total_size=1 << 20)
    pick = discovery.pick_default_model(dirs=[storage, other])
    assert pick is not None and pick["id"] == "small"  # storage/models wins over size


# --------------------------------------------------------------------- #
# Lifecycle against a fake runtime
# --------------------------------------------------------------------- #

FAKE_RUNTIME = r'''
import json, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

if "--help" in sys.argv:
    print("usage: fake [--model X] [--port N] [--host H] [--gpulayers N] "
          "[--contextsize N] [--threads N] [--batchsize N] [--usevulkan] "
          "[--usecuda] [--usecpu] [--quiet] [--skiplauncher] [--noflashattention]")
    sys.exit(0)

port = 5001
for i, a in enumerate(sys.argv):
    if a == "--port" and i + 1 < len(sys.argv):
        port = int(sys.argv[i + 1])

ANSWER = "hello from fake runtime"

class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/v1/models"):
            self._json({"object": "list", "data": [{"id": "fake", "object": "model"}]})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        if not self.path.startswith("/v1/chat/completions"):
            self._json({"error": "not found"}, 404)
            return
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length) or b"{}")
        if payload.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for tok in ["hello ", "from ", "fake ", "runtime"]:
                self.wfile.write(("data: " + json.dumps({"choices": [{"delta": {"content": tok}}]}) + "\n\n").encode())
                self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        else:
            self._json({"choices": [{"message": {"role": "assistant", "content": ANSWER}}]})

ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
'''


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def test_engine_lifecycle_with_fake_runtime(tmp_path, monkeypatch):
    from shared.native_engine import budget
    from shared.native_engine.engine import NativeEngine

    script = tmp_path / "fake_koboldcpp.py"
    script.write_text(FAKE_RUNTIME, encoding="utf-8")
    gguf = _write_fake_gguf(tmp_path / "fake.gguf", total_size=1 << 20)

    monkeypatch.setenv("SHIMS_NATIVE_RUNTIME", f'"{sys.executable}" "{script}"')
    monkeypatch.setenv("SHIMS_NATIVE_PORT", str(_free_port()))

    plan = LaunchPlan(model_path=str(gguf), backend="vulkan", runtime_name="koboldcpp.exe",
                      gpu_layers=32, ctx=8192, threads=4, block_count=32,
                      est_weights_bytes=gguf.stat().st_size, est_kv_bytes=1024)
    monkeypatch.setattr("shared.native_engine.engine.compute_launch_plan",
                        lambda hw, path: plan)

    engine = NativeEngine()
    monkeypatch.setattr(engine, "_profile_hardware", lambda: None)
    try:
        health = engine.start(str(gguf))
        assert health["ready"] is True
        assert health["model"] == "fake"
        assert health["gpu_layers"] == 32
        assert health["backend"] == "vulkan"
        assert budget.reserved_bytes() > 0

        raw = engine.chat_raw([{"role": "user", "content": "hi"}])
        assert raw["content"] == "hello from fake runtime"
        assert raw["tool_calls"] == []

        deltas: list[str] = []
        streamed = engine.chat_stream([{"role": "user", "content": "hi"}], deltas.append)
        assert streamed["content"] == "hello from fake runtime"
        assert "".join(deltas) == "hello from fake runtime"
        assert len(deltas) == 4  # true per-chunk streaming, not one blob

        models = engine.models()
        assert any(m["id"] == "fake" and m["loaded"] for m in models) or engine.loaded_model_id() == "fake"
    finally:
        engine.stop()
    assert engine.health()["running"] is False
    assert engine.health()["ready"] is False
    assert budget.reserved_bytes() == 0


def test_launch_command_drops_unknown_flags(tmp_path):
    from shared.native_engine.runtime import build_launch_command

    plan = LaunchPlan(model_path="m.gguf", backend="vulkan", runtime_name="koboldcpp.exe",
                      gpu_layers=32, ctx=16384, threads=14, block_count=32)
    # koboldcpp 1.117 --help shape: no --cache-prompt/--cache-reuse/--flashattention,
    # flash attention is default-on (only --noflashattention advertised).
    help_text = ("--usecuda --usevulkan --usecpu --model --port --host --gpulayers "
                 "--contextsize --threads --batchsize --noflashattention --quiet --skiplauncher")
    argv = build_launch_command(["kcp"], plan, 5115, help_text)
    joined = " ".join(argv)
    assert "--usevulkan" in argv
    assert "--usecuda" not in argv
    assert "--flashattention" not in argv
    assert "--cache-prompt" not in argv
    assert "--cache-reuse" not in argv
    assert "--quiet" in argv and "--skiplauncher" in argv
    assert "--model" in argv and "m.gguf" in argv
    assert "--contextsize" in argv and "16384" in argv
    # llama-server-style binary: cache/flash flags are picked up when advertised.
    help_text2 = help_text + " --cache-prompt --cache-reuse --flashattention"
    argv2 = build_launch_command(["kcp"], plan, 5115, help_text2)
    assert "--cache-prompt" in argv2 and "--flashattention" in argv2
    i = argv2.index("--cache-reuse")
    assert argv2[i + 1] == "256"
    assert "--port" in joined and "5115" in joined
    # No jinja advertised (ancient binary): no jinja flags.
    assert "--jinja" not in argv and "--jinja_tools" not in argv


def test_launch_command_enables_jinja_tools(tmp_path):
    """koboldcpp ≥1.117 advertises --jinja_tools: tool calls go through the
    GGUF chat template. Without any jinja flag the server silently drops the
    `tools` payload and the model can never emit tool_calls."""
    from shared.native_engine.runtime import build_launch_command

    plan = LaunchPlan(model_path="m.gguf", backend="vulkan", runtime_name="koboldcpp.exe",
                      gpu_layers=32, ctx=16384, threads=14, block_count=32)
    base = ("--usecuda --usevulkan --model --port --host --gpulayers "
            "--contextsize --threads --batchsize --quiet --skiplauncher")
    modern = base + " --jinja --jinja_tools --jinja_kwargs --jinjatemplate --jinjathink"
    argv = build_launch_command(["kcp"], plan, 5115, modern)
    assert "--jinja_tools" in argv
    assert "--jinja" not in argv  # implied by --jinja_tools; never passed twice
    # Older binary with only plain --jinja still gets templating.
    older = build_launch_command(["kcp"], plan, 5115, base + " --jinja")
    assert "--jinja" in older and "--jinja_tools" not in older
    # Parallel slots are passed only when requested and advertised.
    assert "--parallelrequests" not in argv  # plan defaults to 1 slot
    plan4 = LaunchPlan(model_path="m.gguf", backend="vulkan", runtime_name="koboldcpp.exe",
                       gpu_layers=32, ctx=32768, threads=14, block_count=32, parallel_slots=4)
    argv4 = build_launch_command(["kcp"], plan4, 5115, modern + " --parallelrequests")
    i = argv4.index("--parallelrequests")
    assert argv4[i + 1] == "4"
    # Not advertised → dropped, never fatal.
    argv5 = build_launch_command(["kcp"], plan4, 5115, modern)
    assert "--parallelrequests" not in argv5


# --------------------------------------------------------------------- #
# Provider registration
# --------------------------------------------------------------------- #

def test_provider_from_model_native():
    from shared.agent_model_router import _provider_from_model
    assert _provider_from_model("native/foo") == "native"


def test_fallback_chain_gated_on_engine_health(monkeypatch):
    from shared import agent_loop

    class _Healthy:
        def health(self):
            return {"ready": True, "model": "fake-model"}

    monkeypatch.setattr("shared.native_engine.get_engine", lambda: _Healthy())
    chain = agent_loop._effective_fallback_chain()
    assert chain[0] == ("native", "fake-model")
    assert chain[1:] == agent_loop.FALLBACK_CHAIN

    class _Stopped:
        def health(self):
            return {"ready": False, "model": ""}

    monkeypatch.setattr("shared.native_engine.get_engine", lambda: _Stopped())
    assert agent_loop._effective_fallback_chain() == agent_loop.FALLBACK_CHAIN


def test_native_in_streaming_wave_providers():
    from shared import agent_loop
    assert "native" in agent_loop.STREAMING_WAVE_PROVIDERS
