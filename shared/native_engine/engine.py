"""NativeEngine — SHIMS-owned native GGUF inference facade.

A process-wide singleton that owns the inference lifecycle: model discovery,
launch-plan computation, runtime spawn/health/stop, and OpenAI-style chat
(raw + true SSE streaming) over the internal loopback endpoint.

Thread safety: a lock serializes lifecycle transitions (start/stop/load);
chat calls are concurrent and only require a running runtime.
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Callable

from . import budget, discovery
from .runtime import _READY_TIMEOUT_S, NativeRuntime
from .tuning import LaunchPlan, compute_launch_plan


def _default_timeout() -> float:
    """Per-request socket timeout. With parallel slots, a request beyond the
    slot count queues server-side and sends no bytes while it waits — the
    timeout must cover worst-case queue + generation, not just generation.
    Override with ``SHIMS_NATIVE_TIMEOUT``."""
    try:
        return float(os.getenv("SHIMS_NATIVE_TIMEOUT", "900"))
    except ValueError:
        return 900.0


class NativeEngine:
    """Facade for the SHIMS native inference engine (provider ``native``)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._runtime = NativeRuntime()
        self._plan: LaunchPlan | None = None
        self._model_id = ""
        self._ready = False

    # ------------------------------------------------------------------ #
    # Lifecycle (serialized)
    # ------------------------------------------------------------------ #

    def start(self, model_path: str | None = None) -> dict[str, Any]:
        """Pick a model (unless given), compute a launch plan, spawn, wait ready."""
        with self._lock:
            if self._ready and self._runtime.is_running() and not model_path:
                return self.health()
            entry = self._resolve_model(model_path)
            if entry is None:
                raise FileNotFoundError(
                    "no GGUF model found in storage/models, data/models, ~/.shims/models, "
                    "or the LM Studio models dir"
                )
            if (
                self._ready
                and self._runtime.is_running()
                and self._model_id == entry["id"]
            ):
                # Already serving exactly what was asked for.
                return self.health()
            hardware_profile = self._profile_hardware()
            plan = compute_launch_plan(hardware_profile, entry["path"])
            running_plan = getattr(self._runtime, "plan", None)
            if (
                not self._ready
                and self._runtime.is_running()
                and running_plan is not None
                and os.path.normcase(os.path.abspath(str(running_plan.model_path)))
                == os.path.normcase(os.path.abspath(str(entry["path"])))
            ):
                # A previous start() timed out waiting on a huge model load, but
                # the runtime process kept loading and may be serving now — adopt
                # it instead of killing a multi-minute load and starting over.
                self._runtime.wait_ready(timeout=_READY_TIMEOUT_S)
                self._runtime.ensure_watchdog()
                self._plan = running_plan
                self._model_id = entry["id"]
                self._ready = True
                budget.reserve(entry["id"], running_plan.est_weights_bytes, running_plan.est_kv_bytes)
                return self.health()
            self._runtime.stop()
            self._runtime = NativeRuntime()
            self._runtime.start(plan)
            self._plan = plan
            self._model_id = entry["id"]
            self._ready = True
            budget.reserve(entry["id"], plan.est_weights_bytes, plan.est_kv_bytes)
            return self.health()

    def stop(self) -> None:
        """Terminate the runtime and release the memory reservation."""
        with self._lock:
            self._runtime.stop()
            self._ready = False
            self._plan = None
            self._model_id = ""
            budget.clear()

    def unload(self) -> None:
        """Alias for stop(): frees model memory; a later start() reloads."""
        self.stop()

    def ensure_loaded(self, model: str) -> dict[str, Any]:
        """Ensure ``model`` (id/name/path) is the loaded one; reloads if needed."""
        entry = self._resolve_model(model)
        if entry is None:
            raise FileNotFoundError(f"unknown native model: {model}")
        with self._lock:
            if self._ready and self._runtime.is_running() and self._model_id == entry["id"]:
                return self.health()
        return self.start(entry["path"])

    def ensure_running(self) -> bool:
        """Keep-warm hook: report whether the runtime is up.

        Does NOT itself rebuild/restart the runtime on a crash. The runtime's
        own watchdog thread (NativeRuntime._watchdog_loop) already owns crash
        recovery with proper backoff and a stale-process sweep; this method used
        to ALSO call self.start(), which tears down and replaces self._runtime
        while the crashed runtime's own watchdog could be mid-respawn on the
        SAME object via a DIFFERENT lock (NativeRuntime._lock, not
        NativeEngine._lock) — neither serializes against the other. That race
        was a confirmed, reproducible cause of duplicate koboldcpp processes
        (two different LaunchPlans logged at the identical timestamp). Only
        start a fresh runtime here if none exists at all (e.g. after unload()
        cleared the plan, or the watchdog itself gave up entirely)."""
        with self._lock:
            if self._runtime.is_running():
                return True
            plan = self._plan
            watchdog = self._runtime._watchdog
        if plan is None:
            return False
        if watchdog is not None and watchdog.is_alive():
            # The runtime's own watchdog is already recovering it; let it.
            return False
        try:
            self.start(plan.model_path)
            return True
        except Exception:
            self._ready = False
            return False

    def _resolve_model(self, model_path: str | None) -> dict[str, Any] | None:
        if model_path:
            entry = discovery.find_model(model_path)
            if entry is not None:
                return entry
            if os.path.isfile(model_path):
                return {"id": os.path.splitext(os.path.basename(model_path))[0],
                        "path": model_path, "size_bytes": os.path.getsize(model_path)}
            return None
        # Single source of truth for "no arg" model resolution:
        # SHIMS_CHAT_MODEL (the user's Settings/topbar pin, persisted to .env)
        # → SHIMS_NATIVE_MODEL → discovery default. Without this precedence,
        # any code path that calls .start() with no arg falls straight through
        # to discovery.pick_default_model() and loads whatever is in
        # storage/models (currently Llama-8B), silently overriding the user's
        # pin every time the engine is restarted or kicked by a chat retry.
        chat_pin = (os.getenv("SHIMS_CHAT_MODEL") or "").strip()
        if chat_pin:
            entry = discovery.find_model(chat_pin)
            if entry is not None:
                return entry
        env_model = (os.getenv("SHIMS_NATIVE_MODEL") or "").strip()
        if env_model:
            entry = discovery.find_model(env_model)
            if entry is not None:
                return entry
        return discovery.pick_default_model()

    @staticmethod
    def _profile_hardware() -> Any:
        try:
            from shared.neural_governor.hardware_profiler import profile_hardware
            return profile_hardware()
        except Exception:
            # Minimal duck-typed fallback: tuning only needs these three fields.
            import psutil
            return type("HW", (), {
                "total_ram_gb": round(psutil.virtual_memory().total / (1024 ** 3), 2),
                "vram_gb": 0.0,
                "cpu_cores": os.cpu_count() or 4,
            })()

    # ------------------------------------------------------------------ #
    # Status (lock-free reads)
    # ------------------------------------------------------------------ #

    @property
    def base_url(self) -> str:
        """Internal loopback base URL of the running runtime."""
        return self._runtime.base_url

    def health(self) -> dict[str, Any]:
        running = self._runtime.is_running()
        plan = self._plan
        return {
            "running": running,
            "ready": bool(self._ready and running),
            "state": "ready" if (self._ready and running) else ("loading" if running else "stopped"),
            "model": self._model_id,
            "model_path": plan.model_path if plan else "",
            "port": self._runtime.port,
            "gpu_layers": plan.gpu_layers if plan else 0,
            "ctx": plan.ctx if plan else 0,
            "threads": plan.threads if plan else 0,
            "backend": plan.backend if plan else "",
            "uptime_s": self._runtime.status()["uptime_s"],
            "restarts": self._runtime.restarts,
            "launch_reason": plan.reason if plan else "",
        }

    def capabilities(self) -> dict[str, Any]:
        """Best-in-class feature scorecard for the RUNNING engine, derived from the
        actual launch argv so 'complete/best provider' is verifiable, not claimed.

        Maps the flags SHIMS passes to koboldcpp to the techniques the big serving
        stacks are known for (continuous batching = vLLM, prefix KV cache = vLLM
        APC / SGLang RadixAttention, quantized KV, flash attention, speculative
        decoding, tool templating)."""
        argv = self._runtime.status().get("argv", []) or []
        argset = set(argv)

        def val(flag: str) -> str | None:
            try:
                return argv[argv.index(flag) + 1]
            except (ValueError, IndexError):
                return None

        features = {
            "gpu_offload": {"active": ("--usecuda" in argset or "--usevulkan" in argset),
                            "detail": (self._plan.backend if self._plan else "")},
            "flash_attention": {"active": "--noflashattention" not in argset,
                                "detail": "on by default"},
            "kv_cache_quantization": {"active": "--quantkv" in argset,
                                      "detail": {"1": "q8", "2": "q4"}.get(val("--quantkv") or "", "f16")},
            "continuous_batching": {"active": "--parallelrequests" in argset,
                                    "detail": f"{val('--parallelrequests') or 1} slot(s)"},
            "prefix_kv_cache": {"active": "--smartcache" in argset,
                                "detail": f"{val('--smartcache') or 0} snapshot slot(s)"},
            "speculative_decoding": {"active": "--draftmodel" in argset,
                                     "detail": val("--draftmodel") or "off (set SHIMS_NATIVE_DRAFT_MODEL)"},
            "tool_calling_template": {"active": ("--jinja" in argset or "--jinja_tools" in argset),
                                      "detail": "jinja tool templating"},
        }
        active = sum(1 for f in features.values() if f["active"])
        return {
            "ok": True, "engine": "native (koboldcpp core)",
            "running": self._runtime.is_running(), "model": self._model_id,
            "backend": (self._plan.backend if self._plan else ""),
            "ctx": (self._plan.ctx if self._plan else 0),
            "score": f"{active}/{len(features)}", "features": features,
        }

    def loaded_model_id(self) -> str:
        return self._model_id if self._ready and self._runtime.is_running() else ""

    def models(self) -> list[dict[str, Any]]:
        """Discovered GGUFs annotated with loaded flag + machine-fit advice.

        The advisor (a concept none of the stock providers integrate): each
        model gets a rating for THIS machine — measured speed from the perf
        ledger when it exists, otherwise predicted from the GGUF header. Rules
        derive from session measurements + research on Strix Halo (~256 GB/s
        UMA): MoE = fast (few active params); dense >25GB = bandwidth-bound
        (~2-5 tok/s); the qwen35 dense hybrid additionally has no fast Vulkan
        prompt path (chunked Gated Delta Net unsupported); larger-than-RAM =
        mmap crawl."""
        try:
            from . import perf
            measured_all = perf.all_summaries()
        except Exception:
            measured_all = {}
        try:
            import psutil
            total_ram = psutil.virtual_memory().total
        except Exception:
            total_ram = 128 * (1024 ** 3)
        loaded = self.loaded_model_id()
        out = []
        for m in discovery.discover_models():
            entry = {k: v for k, v in m.items() if k != "metadata"}
            entry["loaded"] = bool(loaded and m["id"] == loaded)
            arch = str(entry.get("arch") or "").lower()
            size = int(entry.get("size_bytes") or 0)
            is_moe = "moe" in arch
            measured = measured_all.get(entry["id"])
            rating = None
            if measured:
                entry["measured"] = measured
                try:
                    from . import perf as _p
                    rating = _p.rating_from_measured(measured)
                except Exception:
                    rating = None
            if rating is None:
                if size > int(total_ram * 0.85):
                    rating, advice = "oversized", "larger than RAM: CPU+mmap crawl (~2 tok/s) — use for batch/nightly only"
                elif is_moe:
                    rating, advice = "fast", "MoE — few active params/token, the right fit for this machine"
                elif arch == "qwen35":
                    rating, advice = "slow", "dense hybrid: no fast Vulkan prompt path in current runtime (chunked GDN unsupported)"
                elif size > 25 * (1024 ** 3):
                    rating, advice = "slow", "large dense: memory-bandwidth-bound (~2-5 tok/s gen); consider a Q4 quant + a small same-family draft model (SHIMS_NATIVE_DRAFT_MODEL)"
                else:
                    rating, advice = "ok", "small enough to run comfortably"
            else:
                advice = f"measured on this machine: {measured.get('gen_tps', '?')} tok/s generation"
            entry["rating"] = rating
            entry["advice"] = advice
            out.append(entry)
        return out

    # ------------------------------------------------------------------ #
    # Chat (concurrent; requires a ready runtime)
    # ------------------------------------------------------------------ #

    def _require_ready(self) -> None:
        if not (self._ready and self._runtime.is_running()):
            raise RuntimeError("native engine is not running — start it first")

    def _payload(self, messages: list[dict[str, Any]], stream: bool, kw: dict[str, Any]) -> dict[str, Any]:
        try:
            default_temp = float(os.getenv("SHIMS_CHAT_TEMPERATURE", "0.2"))
        except ValueError:
            default_temp = 0.2
        payload: dict[str, Any] = {
            "model": self._model_id or "native",
            "messages": messages,
            "stream": stream,
            "temperature": float(kw.pop("temperature", default_temp)),
        }
        for key in ("max_tokens", "top_p", "stop", "tools", "reasoning_effort", "context_length",
                    "num_ctx", "chat_template_kwargs",
                    # Structured/constrained output (best-in-class parity): koboldcpp
                    # honors an OpenAI-style response_format (json_object/json_schema)
                    # and a GBNF grammar, so tool-calls and extraction stay valid.
                    "response_format", "grammar", "json_schema"):
            if kw.get(key) is not None:
                payload[key] = kw[key]
        # Hard cap on generation length. Without max_tokens, koboldcpp's OpenAI
        # endpoint generates until it fills the remaining context (ctx - prompt =
        # ~13k tokens) — minutes of runaway output that looks like a hang and, on
        # thinking models, burns the whole budget on internal reasoning. Default
        # to a sane cap (override per-call or via SHIMS_CHAT_MAX_TOKENS).
        if payload.get("max_tokens") is None:
            try:
                payload["max_tokens"] = max(64, int(os.getenv("SHIMS_CHAT_MAX_TOKENS", "1024")))
            except ValueError:
                payload["max_tokens"] = 1024
        return payload

    def chat_raw(self, messages: list[dict[str, Any]], **kw: Any) -> dict[str, Any]:
        """Non-streaming chat. Returns {"content": str, "tool_calls": [...]} —
        the same shape agent_loop._lmstudio_chat_raw returns."""
        import requests
        self._require_ready()
        budget.touch()
        timeout = float(kw.pop("timeout", 0) or _default_timeout())
        payload = self._payload(messages, False, kw)
        r = requests.post(f"{self._runtime.base_url}/v1/chat/completions",
                          json=payload, timeout=timeout)
        r.raise_for_status()
        data = r.json()
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        tool_calls: list[Any] = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            try:
                args = json.loads(fn.get("arguments", "{}"))
            except Exception:
                args = fn.get("arguments") or {}
            tool_calls.append({"function": {"name": fn.get("name", ""), "arguments": args}})
        return {"content": msg.get("content") or "", "tool_calls": tool_calls}

    def chat_stream(self, messages: list[dict[str, Any]],
                    on_delta: Callable[[str], Any], **kw: Any) -> dict[str, Any]:
        """True SSE streaming chat. ``on_delta`` is a sync callable invoked per
        content chunk as it arrives; async wrappers bridge it from agent_loop.
        ``on_reasoning`` (optional kw) receives reasoning_content chunks from
        thinking models so callers can surface the thinking instead of sitting
        silent. Returns {"content", "reasoning", "tool_calls"} like chat_raw."""
        import requests
        self._require_ready()
        budget.touch()
        on_reasoning = kw.pop("on_reasoning", None)
        timeout = float(kw.pop("timeout", 0) or _default_timeout())
        payload = self._payload(messages, True, kw)
        content = ""
        reasoning = ""
        tool_calls_acc: dict[int, dict[str, Any]] = {}
        # Performance ledger: measure ttft + delta count from the real stream.
        _perf_t0 = time.time()
        _perf_state = {"ttft": None, "deltas": 0}
        _inner_on_delta = on_delta

        def on_delta(chunk: str) -> Any:  # noqa: F811 — deliberate wrap
            if _perf_state["ttft"] is None:
                _perf_state["ttft"] = time.time() - _perf_t0
            _perf_state["deltas"] += 1
            return _inner_on_delta(chunk)
        with requests.post(f"{self._runtime.base_url}/v1/chat/completions",
                           json=payload, stream=True, timeout=timeout) as r:
            r.raise_for_status()
            for raw_line in r.iter_lines():
                if not raw_line:
                    continue
                line = raw_line.decode("utf-8", errors="ignore").strip()
                if not line.startswith("data:"):
                    continue
                data_str = line[len("data:"):].strip()
                if data_str == "[DONE]":
                    break
                try:
                    obj = json.loads(data_str)
                except Exception:
                    continue
                delta_obj = (obj.get("choices") or [{}])[0].get("delta") or {}
                for tc in delta_obj.get("tool_calls") or []:
                    idx = tc.get("index", 0)
                    slot = tool_calls_acc.setdefault(idx, {"function": {"name": "", "arguments": ""}})
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        slot["function"]["name"] += fn["name"]
                    if fn.get("arguments"):
                        slot["function"]["arguments"] += fn["arguments"]
                think = delta_obj.get("reasoning_content") or ""
                if think:
                    reasoning += think
                    if on_reasoning is not None:
                        on_reasoning(think)
                delta = delta_obj.get("content") or ""
                if delta:
                    content += delta
                    on_delta(delta)
        tool_calls: list[Any] = []
        for slot in tool_calls_acc.values():
            try:
                args = json.loads(slot["function"]["arguments"] or "{}")
            except Exception:
                args = slot["function"]["arguments"] or {}
            tool_calls.append({"function": {"name": slot["function"]["name"], "arguments": args}})
        # Record measured speed (soft-fail; ~4 chars/token prompt estimate).
        try:
            from . import perf
            if _perf_state["deltas"]:
                prompt_est = sum(len(str(m.get("content") or "")) for m in messages) // 4
                perf.record(self._model_id, prompt_tokens=prompt_est,
                            gen_tokens=_perf_state["deltas"],
                            ttft_s=float(_perf_state["ttft"] or 0.0),
                            total_s=time.time() - _perf_t0)
        except Exception:
            pass
        return {"content": content, "reasoning": reasoning, "tool_calls": tool_calls}


_ENGINE: NativeEngine | None = None
_ENGINE_LOCK = threading.Lock()


def get_engine() -> NativeEngine:
    """Process-wide NativeEngine singleton."""
    global _ENGINE
    with _ENGINE_LOCK:
        if _ENGINE is None:
            _ENGINE = NativeEngine()
        return _ENGINE
