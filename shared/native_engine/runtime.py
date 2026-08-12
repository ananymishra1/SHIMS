"""koboldcpp child-process runtime for the SHIMS native engine.

SHIMS embeds and fully owns the koboldcpp binary: it is spawned as a child
process with SHIMS-computed flags, never touched by the user directly, and
only talked to over an internal loopback OpenAI-compatible endpoint.

The launch command is derived from the binary's own ``--help`` output
(probed once, cached): optional flags that the binary does not advertise are
dropped silently so a version drift can never crash a launch.

Layout note: this module is deliberately a thin process/HTTP wrapper so an
in-process llama-cpp-python backend can be slotted in later behind the same
``NativeRuntime`` interface.
"""
from __future__ import annotations

import os
import shlex
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from .tuning import LaunchPlan

_DEFAULT_PORT = 5115
# Large models (40B+ Q8) can take 5-10 min to load on Vulkan — keep this high
# and overridable. If a load outlasts it, engine.start() can still ADOPT the
# runtime later instead of killing the load (see NativeEngine.start).
_READY_TIMEOUT_S = float(os.getenv("SHIMS_NATIVE_READY_TIMEOUT_S", "600"))
_HELP_CACHE: dict[str, str] = {}
_MAX_RESTARTS = 3


def _max_restarts() -> int:
    """Consecutive crash-restarts before the watchdog pauses (then resets and
    keeps trying). Higher than the old hard cap of 3 so a flaky big-model load
    is not abandoned. Override with SHIMS_NATIVE_MAX_RESTARTS."""
    try:
        return max(1, int(os.getenv("SHIMS_NATIVE_MAX_RESTARTS", "10")))
    except ValueError:
        return 10


def _stable_reset_s() -> float:
    """Uptime after which the restart budget is forgiven. Override with
    SHIMS_NATIVE_STABLE_RESET_S."""
    try:
        return max(30.0, float(os.getenv("SHIMS_NATIVE_STABLE_RESET_S", "300")))
    except ValueError:
        return 300.0


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _log_file():
    log_dir = _repo_root() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return open(log_dir / "native_engine.log", "a", encoding="utf-8", buffering=1)


def locate_runtime_command(runtime_name: str = "koboldcpp.exe") -> list[str]:
    """Resolve the runtime executable to an argv prefix.

    ``SHIMS_NATIVE_RUNTIME`` overrides everything and may be a full command
    line (e.g. ``python fake_server.py`` for tests). Otherwise the bundled
    binaries under ``tools/koboldcpp/`` are used, with the ``storage/models``
    copies as a fallback.
    """
    override = (os.getenv("SHIMS_NATIVE_RUNTIME") or "").strip()
    if override:
        # posix=False keeps Windows paths intact but retains surrounding
        # quotes in tokens — strip them so argv[0] is a real executable path.
        return [t.strip('"').strip("'") for t in shlex.split(override, posix=(os.name != "nt"))]
    candidates = [
        _repo_root() / "tools" / "koboldcpp" / runtime_name,
        _repo_root() / "storage" / "models" / runtime_name,
    ]
    for path in candidates:
        if path.is_file():
            return [str(path)]
    raise FileNotFoundError(
        f"native runtime '{runtime_name}' not found in tools/koboldcpp or storage/models; "
        "set SHIMS_NATIVE_RUNTIME to a koboldcpp-compatible executable"
    )


def probe_help(cmd: list[str]) -> str:
    """Run ``<cmd> --help`` once and cache the output. Empty on any failure."""
    key = "\x00".join(cmd)
    if key in _HELP_CACHE:
        return _HELP_CACHE[key]
    text = ""
    try:
        out = subprocess.run(
            cmd + ["--help"],
            capture_output=True, text=True, timeout=180,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        text = (out.stdout or "") + "\n" + (out.stderr or "")
    except Exception:
        text = ""
    _HELP_CACHE[key] = text
    return text


def _resolve_draft_model_path(ref: str) -> str:
    """Resolve a draft-model id or path to a GGUF file path ('' if not found)."""
    ref = ref.strip()
    if not ref:
        return ""
    if os.path.isfile(ref):
        return ref
    try:
        from .discovery import find_model
        entry = find_model(ref)
        if entry:
            return str(entry["path"])
    except Exception:
        pass
    return ""


def build_launch_command(cmd: list[str], plan: LaunchPlan, port: int, help_text: str) -> list[str]:
    """Assemble the koboldcpp argv from a launch plan + advertised flags.

    Core flags (--model/--port/--host/--gpulayers/--contextsize/--threads/
    --batchsize) are universal koboldcpp flags and always passed. Optional
    flags are only added when the binary's --help advertises them — unknown
    flags are dropped, never fatal.
    """
    argv = list(cmd) + [
        "--model", plan.model_path,
        "--port", str(port),
        "--host", "127.0.0.1",
        "--gpulayers", str(plan.gpu_layers),
        "--contextsize", str(plan.ctx),
        "--threads", str(plan.threads),
        "--batchsize", str(plan.batch_size),
    ]
    low = help_text.lower()

    def advertised(flag: str) -> bool:
        return not low or flag in low  # probe failed → assume modern binary

    # Backend selection.
    if plan.backend == "cuda" and advertised("--usecuda"):
        argv.append("--usecuda")
    elif plan.backend == "vulkan" and advertised("--usevulkan"):
        argv.append("--usevulkan")
    elif plan.backend == "cpu" and "--usecpu" in low:
        argv.append("--usecpu")
    # Flash attention: koboldcpp enables it by default (it only advertises
    # --noflashattention); llama-server-style binaries take an explicit flag.
    if "--flashattention" in low:
        argv.append("--flashattention")
    # Prompt-cache reuse (llama-server style flags; koboldcpp does this
    # internally and does not advertise these — they are then dropped).
    if "--cache-prompt" in low:
        argv.append("--cache-prompt")
    if "--cache-reuse" in low:
        argv += ["--cache-reuse", "256"]
    # KV snapshot caching across requests (koboldcpp --smartcache): without it
    # every chat turn re-evaluates the whole stable system prompt — measured
    # ~20s of dead time before the first reasoning token on a 35B Q8. With
    # snapshots, repeat turns resume from the cached prefix instead.
    # Use advertised() (not a bare substring test): when the one-shot --help
    # probe is slow/empty at boot it gets cached "" for the process, which
    # silently DROPPED --smartcache and left every turn reprocessing the whole
    # prompt (the exact ~15s TTFT we hit). koboldcpp always supports it, so
    # apply it whenever the probe advertises it OR the probe failed.
    _smartcache = os.getenv("SHIMS_NATIVE_SMARTCACHE", "8").strip().lower()
    if advertised("--smartcache") and _smartcache not in ("", "0", "off", "no", "false"):
        argv += ["--smartcache", _smartcache]
    # Oversized models (weights > memory pool) must page from disk — koboldcpp
    # defaults to mmap OFF and dies allocating the full weight set.
    if getattr(plan, "use_mmap", False) and advertised("--usemmap"):
        argv.append("--usemmap")
    # KV-cache quantization: with flash attention the KV cache can be stored at
    # q8/q4 instead of f16, roughly halving/quartering its memory. OFF by
    # default: verified via logs/native_engine.log that --quantkv 1 crashes this
    # koboldcpp build 100% of the time on Gemma-4 (SWA + fused Gated Delta Net
    # attention) on Vulkan — every attempt dies at "attach_threadpool: call"
    # right after KV-cache reservation, before the model finishes loading.
    # Stability wins; opt in with SHIMS_NATIVE_QUANTKV=1 only after confirming
    # your model/backend combination tolerates it.
    quantkv = (os.getenv("SHIMS_NATIVE_QUANTKV", "0") or "0").strip()
    if quantkv not in ("", "0") and advertised("--quantkv"):
        argv += ["--quantkv", quantkv]
    # Jinja chat templating: the GGUF's chat template is what renders the
    # `tools` payload into the prompt — without it the server silently drops
    # tool schemas and the model can never emit tool_calls. --jinja_tools
    # (koboldcpp ≥1.117) implies --jinja and handles tool calls through the
    # template; plain --jinja is the fallback for older binaries.
    if "--jinja_tools" in low or "--jinja-tools" in low or "--jinjatools" in low:
        argv.append("--jinja_tools")
    elif advertised("--jinja"):
        argv.append("--jinja")
    # Parallel request slots (continuous batching). Without this every wave,
    # swarm agent, and background job queues behind a single slot.
    if getattr(plan, "parallel_slots", 1) > 1 and advertised("--parallelrequests"):
        argv += ["--parallelrequests", str(plan.parallel_slots)]
    # Speculative decoding: a small, tokenizer-compatible draft model proposes
    # tokens the main model verifies in one pass — ~1.5-2x on DENSE models. Off
    # unless SHIMS_NATIVE_DRAFT_MODEL names a model id/path (it MUST share the main
    # model's tokenizer family, e.g. a small Qwen draft for a Qwen main).
    draft_ref = (os.getenv("SHIMS_NATIVE_DRAFT_MODEL") or "").strip()
    if draft_ref and advertised("--draftmodel"):
        draft_path = _resolve_draft_model_path(draft_ref)
        if draft_path:
            argv += ["--draftmodel", draft_path]
            amount = (os.getenv("SHIMS_NATIVE_DRAFT_AMOUNT") or "").strip()
            if amount and "--draftamount" in low:
                argv += ["--draftamount", amount]
    # Headless operation niceties.
    if "--quiet" in low:
        argv.append("--quiet")
    if "--skiplauncher" in low:
        argv.append("--skiplauncher")
    return argv


class NativeRuntime:
    """Owns one koboldcpp child process: spawn, health, restart, kill."""

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._log = None
        self._argv: list[str] = []
        self.port = int(os.getenv("SHIMS_NATIVE_PORT", str(_DEFAULT_PORT)) or _DEFAULT_PORT)
        self.plan: LaunchPlan | None = None
        self.started_at: float | None = None
        self.restarts = 0
        self._stop_requested = False
        self._watchdog: threading.Thread | None = None
        self._lock = threading.Lock()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self, plan: LaunchPlan) -> None:
        """Spawn the runtime and block until the API answers (or timeout)."""
        with self._lock:
            self._stop_requested = False
            self.plan = plan
            self._spawn(plan)
        self.wait_ready(timeout=_READY_TIMEOUT_S)
        self._start_watchdog()

    def _kill_stale_runtimes(self) -> None:
        """Reap every stray SHIMS engine before (re)spawning, then wait for death.

        SHIMS exclusively owns the koboldcpp binary, so any koboldcpp process that
        is NOT our current child is a stray from a crashed/replaced backend (on
        Windows the detached child survives its parent). Two 100 GB+ models paging
        from disk at once exhausts memory and is the observed "crash / won't
        recover" symptom. We reap by exe name — NOT just by port — because a stray
        can still be loading and not yet bound to any port. Verified with
        wait_procs so the new engine never races a dying one for the same memory.
        """
        try:
            import psutil
        except Exception:
            return
        exe_names = {"koboldcpp.exe", "koboldcpp-nocuda.exe", "koboldcpp"}
        mine = self._proc.pid if self._proc is not None else None
        victims = []
        for proc in psutil.process_iter(["pid", "name"]):
            try:
                if proc.pid == mine:
                    continue
                if (proc.info.get("name") or "").lower() in exe_names:
                    victims.append(proc)
            except Exception:
                continue
        for proc in victims:
            try:
                proc.kill()
            except Exception:
                pass
        if victims:
            try:
                psutil.wait_procs(victims, timeout=15)
            except Exception:
                pass

    def _spawn(self, plan: LaunchPlan) -> None:
        cmd = locate_runtime_command(plan.runtime_name)
        help_text = probe_help(cmd)
        self._argv = build_launch_command(cmd, plan, self.port, help_text)
        self._kill_stale_runtimes()
        if self._log is None:
            self._log = _log_file()
        self._log.write(f"\n=== native engine launch {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
        self._log.write("argv: " + " ".join(self._argv) + "\n")
        creationflags = (
            getattr(subprocess, "CREATE_NO_WINDOW", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
        self._proc = subprocess.Popen(
            self._argv,
            stdout=self._log, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        self.started_at = time.time()

    def wait_ready(self, timeout: float = _READY_TIMEOUT_S) -> None:
        """Poll the loopback API until the model is loaded and serving."""
        import requests
        deadline = time.time() + timeout
        last_error = ""
        while time.time() < deadline:
            if not self.is_running():
                raise RuntimeError(
                    f"native runtime exited during startup (code {self._proc.returncode}); "
                    "see logs/native_engine.log"
                )
            try:
                r = requests.get(f"{self.base_url}/v1/models", timeout=5)
                if r.status_code == 200:
                    return
                last_error = f"http {r.status_code}"
            except Exception as exc:
                last_error = str(exc)[:120]
            time.sleep(2.0)
        raise TimeoutError(f"native runtime not ready after {timeout:.0f}s ({last_error})")

    def _start_watchdog(self) -> None:
        if self._watchdog and self._watchdog.is_alive():
            return
        self._watchdog = threading.Thread(target=self._watchdog_loop, daemon=True,
                                          name="native-engine-watchdog")
        self._watchdog.start()

    def ensure_watchdog(self) -> None:
        """Public wrapper for engine-level adoption of an already-running
        runtime (e.g. after a start() call timed out during a huge model load
        but the process kept loading and became ready later)."""
        self._start_watchdog()

    def _watchdog_loop(self) -> None:
        """Self-healing crash auto-restart.

        Two properties make the engine sturdy: (1) the restart budget RESETS once
        the runtime has run healthy for a while, so a burst of transient crashes
        never permanently exhausts it (the 'won't restart' symptom); (2) when the
        budget is exhausted it PAUSES and keeps trying rather than dying forever,
        so a slow-to-recover machine (e.g. a 140 GB model paging from disk) is
        never abandoned. Backoff is capped so restarts stay responsive."""
        max_restarts = _max_restarts()
        stable_reset_s = _stable_reset_s()
        while True:
            time.sleep(2.0)
            with self._lock:
                if self._stop_requested:
                    return
                if self.is_running():
                    # Ran healthy long enough → forgive earlier crashes.
                    if self.restarts and self.started_at and (time.time() - self.started_at) > stable_reset_s:
                        self.restarts = 0
                    continue
                if self.plan is None:
                    continue
                if self.restarts >= max_restarts:
                    self._log.write(f"native runtime crashed; {max_restarts} restarts exhausted, "
                                    f"pausing {stable_reset_s:.0f}s then retrying (never gives up)\n")
                    self.restarts = 0
                    pause = stable_reset_s
                else:
                    self.restarts += 1
                    pause = min(60.0, 2.0 * (2 ** (self.restarts - 1)))
                    self._log.write(f"native runtime crashed; restart {self.restarts}/{max_restarts} in {pause:.0f}s\n")
            time.sleep(pause)
            with self._lock:
                if self._stop_requested or self.plan is None:
                    return
                try:
                    self._spawn(self.plan)
                except Exception as exc:
                    self._log.write(f"native runtime respawn failed: {exc}\n")
                    continue
            try:
                self.wait_ready(timeout=_READY_TIMEOUT_S)
            except Exception as exc:
                self._log.write(f"native runtime respawn did not become ready: {exc}\n")

    def stop(self) -> None:
        """Terminate the child process tree (Windows console-less safe)."""
        with self._lock:
            self._stop_requested = True
            proc = self._proc
            self._proc = None
            self.plan = None
        if proc is None or proc.poll() is not None:
            return
        try:
            import psutil
            parent = psutil.Process(proc.pid)
            for child in parent.children(recursive=True):
                try:
                    child.kill()
                except Exception:
                    pass
            parent.terminate()
            try:
                parent.wait(timeout=10)
            except Exception:
                parent.kill()
        except Exception:
            try:
                proc.terminate()
                proc.wait(timeout=10)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    def status(self) -> dict[str, Any]:
        uptime = (time.time() - self.started_at) if (self.started_at and self.is_running()) else 0.0
        return {
            "running": self.is_running(),
            "port": self.port,
            "base_url": self.base_url,
            "uptime_s": round(uptime, 1),
            "restarts": self.restarts,
            "argv": self._argv if self.is_running() else [],
        }
