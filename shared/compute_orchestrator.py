"""ComputeOrchestrator — single authority over the machine's whole memory.

Timeshares the box between three workloads:

- **native-llm** — the SHIMS-owned GGUF engine (``shared/native_engine``); its
  reservation is read from ``native_engine.budget.report()``.
- **comfyui** — local image generation, only run during idle/downtime so it
  never starves chat. Managed as an idle-drained job queue.
- **fish-speech** — fish-speech-2 (OpenAudio S2-Pro) TTS server lifecycle;
  started on demand, stopped after an idle TTL.

Nothing is hardcoded to a specific machine: free RAM is probed via psutil,
reservations come from env-tunable estimates, and installs are located by
probing the filesystem / HTTP endpoints.

Thread safety: a single lock guards all mutable state. The async backend
drives ``tick()`` every 30 s from the lifespan task; chat turns call
``touch_activity()``.
"""
from __future__ import annotations

import os
import subprocess
import threading
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Callable

_GB = 1024 ** 3
_COMFY_READY_TIMEOUT_S = 180.0
_FISH_READY_TIMEOUT_S = 240.0  # ~11 GB checkpoint load
_PROBE_CACHE_S = 10.0


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


def _free_ram_bytes() -> int:
    import psutil
    return int(psutil.virtual_memory().available)


def _kill_process_tree(proc: subprocess.Popen) -> None:
    """Terminate a spawned child tree (same approach as native runtime.py)."""
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


class ComputeOrchestrator:
    """Budget ledger + idle state machine + workload lifecycle manager."""

    def __init__(
        self,
        output_dir: Path | None = None,
        popen: Callable[..., Any] | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._now: Callable[[], float] = time.time
        self._sleep: Callable[[float], None] = time.sleep
        self._popen = popen or subprocess.Popen
        self.output_dir = Path(output_dir) if output_dir else _repo_root() / "data" / "media" / "orchestrator"

        # Activity tracking.
        self._last_activity = self._now()
        self._activity_seq = 0

        # ComfyUI lifecycle.
        self._comfy_proc: Any = None
        self._comfy_owned = False
        self._comfy_state = "unknown"  # unknown|external|owned|suspended|unavailable
        self._comfy_log = None
        self._comfy_probe_at = 0.0
        self._comfy_probe_ok = False

        # fish-speech lifecycle.
        self._fish_proc: Any = None
        self._fish_owned = False
        self._fish_state = "unknown"
        self._fish_log = None
        self._fish_last_used = 0.0
        self._fish_probe_at = 0.0
        self._fish_probe_ok = False

        # Image job queue (in-memory; persistent-ish for process lifetime).
        self._jobs: dict[str, dict[str, Any]] = {}
        self._pending: list[str] = []
        self._draining = False
        self._drain_thread: threading.Thread | None = None
        self._drain_seq = 0  # external-activity seq when the drain session began
        self._stop_dequeue = threading.Event()
        self._suspend_requested = False
        self._suspend_since = 0.0
        self._current_job_id: str | None = None

    # ------------------------------------------------------------------ #
    # Env-derived policy
    # ------------------------------------------------------------------ #

    def comfy_reserve_bytes(self) -> int:
        return int(_env_float("SHIMS_COMFYUI_RESERVE_GB", 12.0) * _GB)

    def fish_reserve_bytes(self) -> int:
        return int(_env_float("SHIMS_FISH_RESERVE_GB", 8.0) * _GB)

    def idle_threshold_s(self) -> float:
        return _env_float("SHIMS_IDLE_MIN", 5.0) * 60.0

    def fish_idle_threshold_s(self) -> float:
        return _env_float("SHIMS_FISH_IDLE_MIN", 15.0) * 60.0

    def drain_timeout_s(self) -> float:
        return _env_float("SHIMS_COMFYUI_DRAIN_S", 120.0)

    # ------------------------------------------------------------------ #
    # Activity tracking / idle state
    # ------------------------------------------------------------------ #

    def touch_activity(self, *, internal: bool = False) -> None:
        """Mark the machine as used. Chat/agent turns call this; orchestrator
        job starts call it with ``internal=True`` so the timestamp reflects
        real use without counting as an interrupt to an active drain."""
        with self._lock:
            self._last_activity = self._now()
            if not internal:
                self._activity_seq += 1

    def idle_seconds(self) -> float:
        with self._lock:
            return max(0.0, self._now() - self._last_activity)

    def is_idle(self) -> bool:
        return self.idle_seconds() >= self.idle_threshold_s()

    # ------------------------------------------------------------------ #
    # Budget ledger
    # ------------------------------------------------------------------ #

    def _native_report(self) -> dict[str, Any]:
        try:
            from shared.native_engine import budget as native_budget
            return native_budget.report()
        except Exception:
            return {"model": "", "reserved_bytes": 0}

    def _comfy_running(self) -> bool:
        with self._lock:
            if self._comfy_owned:
                return self._comfy_proc is not None and self._comfy_proc.poll() is None
            return self._comfy_state == "external" and self._comfy_probe_ok

    def _fish_running(self) -> bool:
        with self._lock:
            if self._fish_owned:
                return self._fish_proc is not None and self._fish_proc.poll() is None
            return self._fish_state == "external" and self._fish_probe_ok

    def headroom_bytes(self) -> int:
        """Free RAM minus every active workload's reservation."""
        native_reserved = int(self._native_report().get("reserved_bytes") or 0)
        used = native_reserved
        if self._comfy_running():
            used += self.comfy_reserve_bytes()
        if self._fish_running():
            used += self.fish_reserve_bytes()
        return max(0, _free_ram_bytes() - used)

    def can_afford(self, workload: str) -> bool:
        reserve = {"comfyui": self.comfy_reserve_bytes, "fish": self.fish_reserve_bytes}[workload]()
        return self.headroom_bytes() >= reserve

    def system_busy_for(self, workload: str) -> bool:
        """True when the workload should not start now (active use or no headroom)."""
        return (not self.is_idle()) or (not self.can_afford(workload))

    def status(self) -> dict[str, Any]:
        native = self._native_report()
        with self._lock:
            pending = [dict(self._jobs[j]) for j in self._pending if j in self._jobs]
            recent = sorted(self._jobs.values(), key=lambda j: j["created_at"], reverse=True)[:10]
            fish_idle = round(max(0.0, self._now() - self._fish_last_used), 1) if self._fish_last_used else None
        comfy_running = self._comfy_running()
        fish_running = self._fish_running()
        return {
            "ok": True,
            "workloads": {
                "native_llm": {
                    "model": native.get("model") or "",
                    "running": bool(native.get("model")),
                    "reserved_bytes": int(native.get("reserved_bytes") or 0),
                },
                "comfyui": {
                    "state": self._comfy_state,
                    "running": comfy_running,
                    "owned": self._comfy_owned,
                    "reserve_bytes": self.comfy_reserve_bytes(),
                    "reserved_bytes": self.comfy_reserve_bytes() if comfy_running else 0,
                },
                "fish_speech": {
                    "state": self._fish_state,
                    "running": fish_running,
                    "owned": self._fish_owned,
                    "reserve_bytes": self.fish_reserve_bytes(),
                    "reserved_bytes": self.fish_reserve_bytes() if fish_running else 0,
                },
            },
            "memory": {
                "free_ram_bytes": _free_ram_bytes(),
                "headroom_bytes": self.headroom_bytes(),
            },
            "idle": {
                "idle_seconds": round(self.idle_seconds(), 1),
                "idle_threshold_s": self.idle_threshold_s(),
                "is_idle": self.is_idle(),
                "fish_idle_seconds": fish_idle,
                "fish_idle_threshold_s": self.fish_idle_threshold_s(),
            },
            "queue": {
                "pending": len(pending),
                "draining": self._draining,
                "current_job_id": self._current_job_id,
                "pending_jobs": pending,
                "recent_jobs": [dict(j) for j in recent],
            },
        }

    # ------------------------------------------------------------------ #
    # Probes (thin, monkeypatchable)
    # ------------------------------------------------------------------ #

    def _comfy_url(self) -> str:
        return (os.getenv("COMFYUI_URL") or "http://127.0.0.1:8188").rstrip("/")

    def _fish_base_url(self) -> str:
        raw = (os.getenv("SHIMS_FISH_URL") or "http://127.0.0.1:8090/v1/tts").rstrip("/")
        return raw[: -len("/v1/tts")] if raw.endswith("/v1/tts") else raw

    def _comfy_reachable(self) -> bool:
        now = self._now()
        if now - self._comfy_probe_at < _PROBE_CACHE_S:
            return self._comfy_probe_ok
        ok = False
        try:
            with urllib.request.urlopen(self._comfy_url() + "/system_stats", timeout=3.0):
                ok = True
        except Exception:
            ok = False
        self._comfy_probe_at = now
        self._comfy_probe_ok = ok
        return ok

    def _fish_reachable(self) -> bool:
        now = self._now()
        if now - self._fish_probe_at < _PROBE_CACHE_S:
            return self._fish_probe_ok
        ok = False
        try:
            with urllib.request.urlopen(self._fish_base_url() + "/v1/health", timeout=3.0):
                ok = True
        except Exception:
            ok = False
        self._fish_probe_at = now
        self._fish_probe_ok = ok
        return ok

    def comfy_available(self) -> bool:
        """True when ComfyUI can take a job right now (running, not suspended)."""
        with self._lock:
            if self._comfy_state in {"suspended", "unavailable"}:
                return False
            if self._comfy_owned and self._comfy_proc is not None and self._comfy_proc.poll() is None:
                return True
        return self._comfy_reachable()

    def comfy_launchable(self) -> bool:
        """True when a ComfyUI install exists that the orchestrator can spawn."""
        with self._lock:
            if self._comfy_state == "unavailable":
                return False
        return self._locate_comfyui() is not None

    # ------------------------------------------------------------------ #
    # Subprocess helpers (patterns from shared/native_engine/runtime.py)
    # ------------------------------------------------------------------ #

    def _open_log(self, name: str):
        log_dir = _repo_root() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        return open(log_dir / name, "a", encoding="utf-8", buffering=1)

    def _spawn(self, argv: list[str], cwd: Path, log) -> Any:
        log.write(f"\n=== orchestrator launch {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
        log.write("argv: " + " ".join(argv) + "\n")
        creationflags = (
            getattr(subprocess, "CREATE_NO_WINDOW", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
        return self._popen(
            argv,
            stdout=log, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            cwd=str(cwd),
            creationflags=creationflags,
        )

    def _wait_ready(self, prober: Callable[[], bool], timeout: float, what: str) -> bool:
        deadline = self._now() + timeout
        while self._now() < deadline:
            if prober():
                return True
            self._sleep(2.0)
        return False

    # ------------------------------------------------------------------ #
    # ComfyUI lifecycle
    # ------------------------------------------------------------------ #

    def _locate_comfyui(self) -> tuple[Path, Path] | None:
        """Find a ComfyUI install → (python_exe, comfy_dir), or None."""
        candidates: list[Path] = []
        env_dir = (os.getenv("COMFYUI_DIR") or "").strip()
        if env_dir:
            candidates.append(Path(env_dir))
        candidates += [
            Path("C:/ComfyUI"),
            Path("C:/d/ComfyUI"),
            Path.home() / "ComfyUI",
            Path("C:/Users/direc/AppData/Local/AMD/AI_Bundle/ComfyUI/ComfyUI"),
        ]
        for directory in candidates:
            if not (directory / "main.py").is_file():
                continue
            for py in (
                directory / "venv" / "Scripts" / "python.exe",
                directory / "venv" / "bin" / "python",
                directory / "python_embeded" / "python.exe",
                directory.parent / "venv" / "Scripts" / "python.exe",
                directory.parent / "python_embeded" / "python.exe",
            ):
                if py.is_file():
                    return py, directory
        return None

    def ensure_comfyui(self) -> bool:
        """Make ComfyUI reachable: adopt an external server or spawn our own."""
        if self._comfy_reachable():
            with self._lock:
                if not (self._comfy_owned and self._comfy_proc is not None
                        and self._comfy_proc.poll() is None):
                    self._comfy_state = "external"
                    self._comfy_owned = False
            return True
        with self._lock:
            if self._comfy_owned and self._comfy_proc is not None and self._comfy_proc.poll() is None:
                pass  # still loading; fall through to wait
            elif self._comfy_state == "unavailable":
                return False
            else:
                located = self._locate_comfyui()
                if located is None:
                    self._comfy_state = "unavailable"
                    return False
                py, directory = located
                port = urllib.parse.urlparse(self._comfy_url()).port or 8188
                if self._comfy_log is None:
                    self._comfy_log = self._open_log("orchestrator_comfyui.log")
                try:
                    self._comfy_proc = self._spawn(
                        [str(py), "main.py", "--port", str(port), "--listen", "127.0.0.1"],
                        directory, self._comfy_log,
                    )
                except Exception:
                    self._comfy_state = "unavailable"
                    return False
                self._comfy_owned = True
                self._comfy_state = "owned"
        if self._wait_ready(self._comfy_reachable_fresh, _COMFY_READY_TIMEOUT_S, "comfyui"):
            return True
        with self._lock:
            self._comfy_state = "unavailable"
        return False

    def _comfy_reachable_fresh(self) -> bool:
        self._comfy_probe_at = 0.0
        return self._comfy_reachable()

    def _fish_reachable_fresh(self) -> bool:
        self._fish_probe_at = 0.0
        return self._fish_reachable()

    def _suspend_comfyui(self) -> None:
        """Stop sending jobs; kill the server only if SHIMS launched it."""
        with self._lock:
            proc = self._comfy_proc if self._comfy_owned else None
            self._comfy_proc = None
            was_owned = self._comfy_owned
            self._comfy_owned = False
            self._comfy_state = "suspended"
            self._suspend_requested = False
            self._suspend_since = 0.0
        if was_owned and proc is not None:
            _kill_process_tree(proc)

    # ------------------------------------------------------------------ #
    # Image job queue
    # ------------------------------------------------------------------ #

    def queue_image_job(self, prompt: str, negative: str = "", width: int = 1024,
                        height: int = 1024, purpose: str = "general") -> str:
        job_id = f"img_{uuid.uuid4().hex[:12]}"
        with self._lock:
            self._jobs[job_id] = {
                "id": job_id,
                "prompt": prompt,
                "negative": negative,
                "width": int(width),
                "height": int(height),
                "purpose": purpose,
                "status": "queued",
                "created_at": self._now(),
                "started_at": None,
                "finished_at": None,
                "path": "",
                "url": "",
                "error": "",
            }
            self._pending.append(job_id)
        return job_id

    def job_status(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else None

    def _next_job(self) -> dict[str, Any] | None:
        with self._lock:
            while self._pending:
                job_id = self._pending.pop(0)
                job = self._jobs.get(job_id)
                if job and job["status"] == "queued":
                    return job
        return None

    def _default_generate(self, job: dict[str, Any], out_path: Path) -> dict[str, Any]:
        """Run one job through the existing ComfyUI HTTP client."""
        import asyncio
        from shared.amd_acceleration import generate_comfy_image
        # NOTE: build_comfy_text_to_image_workflow has no negative-prompt input;
        # job["negative"] is recorded for future workflow builders.
        return asyncio.run(generate_comfy_image(
            job["prompt"], output_path=out_path,
            width=job["width"], height=job["height"],
        ))

    def _run_job(self, job: dict[str, Any]) -> None:
        self.touch_activity(internal=True)
        out_path = self.output_dir / f"{job['id']}.png"
        with self._lock:
            job["status"] = "running"
            job["started_at"] = self._now()
            self._current_job_id = job["id"]
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            result = self._generate_fn(job, out_path)
            with self._lock:
                if result and result.get("ok"):
                    job["status"] = "done"
                    job["path"] = str(result.get("path") or out_path)
                    job["url"] = f"/media/files/orchestrator/{Path(job['path']).name}"
                else:
                    job["status"] = "failed"
                    job["error"] = str((result or {}).get("error") or "generation failed")[:240]
        except Exception as exc:
            with self._lock:
                job["status"] = "failed"
                job["error"] = str(exc)[:240]
        finally:
            with self._lock:
                job["finished_at"] = self._now()
                self._current_job_id = None

    _generate_fn = _default_generate  # instance-overridable in tests

    def _drain_loop(self) -> None:
        with self._lock:
            self._draining = True
        try:
            while not self._stop_dequeue.is_set():
                job = self._next_job()
                if job is None:
                    break
                self._run_job(job)
        finally:
            with self._lock:
                self._draining = False
            self._maybe_suspend()

    def _start_drain(self) -> None:
        with self._lock:
            if self._draining:
                return
            self._stop_dequeue.clear()
            self._drain_seq = self._activity_seq
            self._draining = True
            self._drain_thread = threading.Thread(
                target=self._drain_body, daemon=True, name="orchestrator-comfy-drain")
            self._drain_thread.start()

    def _drain_body(self) -> None:
        try:
            self._drain_loop()
        except Exception:
            with self._lock:
                self._draining = False

    def _maybe_suspend(self) -> None:
        with self._lock:
            should = self._suspend_requested and self._current_job_id is None and not self._draining
        if should:
            self._suspend_comfyui()

    # ------------------------------------------------------------------ #
    # fish-speech lifecycle
    # ------------------------------------------------------------------ #

    def _locate_fish(self) -> tuple[Path, Path] | None:
        """(venv_python, fish_dir) for the fish-speech-2 install, or None."""
        fish_dir = Path(os.getenv("SHIMS_FISH_DIR") or (_repo_root() / "tools" / "fish-speech-2"))
        fish_py = Path(os.getenv("SHIMS_FISH_PYTHON") or (fish_dir / "venv" / "Scripts" / "python.exe"))
        if not fish_py.is_file() or not (fish_dir / "tools" / "api_server.py").is_file():
            return None
        return fish_py, fish_dir

    def fish_used(self) -> None:
        """Record a TTS call; resets the fish idle-stop timer."""
        with self._lock:
            self._fish_last_used = self._now()

    def ensure_fish(self, *, proactive: bool = False) -> bool:
        """Make the fish-speech TTS server reachable.

        ``proactive=True`` (orchestrator-initiated) additionally requires the
        machine to be idle; an explicit TTS request only requires headroom.
        Never adopts/kills a server it didn't start beyond marking it external.
        """
        if self._fish_reachable():
            with self._lock:
                if not (self._fish_owned and self._fish_proc is not None
                        and self._fish_proc.poll() is None):
                    self._fish_state = "external"
                    self._fish_owned = False
            self.fish_used()
            return True
        if proactive and not self.is_idle():
            return False
        if not self.can_afford("fish"):
            return False
        with self._lock:
            if self._fish_owned and self._fish_proc is not None and self._fish_proc.poll() is None:
                pass  # still loading; fall through to wait
            else:
                located = self._locate_fish()
                if located is None:
                    self._fish_state = "unavailable"
                    return False
                fish_py, fish_dir = located
                port = urllib.parse.urlparse(self._fish_base_url()).port or 8080
                argv = [
                    str(fish_py), "-m", "tools.api_server",
                    "--listen", f"127.0.0.1:{port}",
                    "--llama-checkpoint-path", "checkpoints/s2-pro",
                    "--decoder-checkpoint-path", "checkpoints/s2-pro/codec.pth",
                ]
                device = (os.getenv("SHIMS_FISH_DEVICE") or "").strip()
                if device:
                    argv += ["--device", device]
                if self._fish_log is None:
                    self._fish_log = self._open_log("orchestrator_fish.log")
                try:
                    self._fish_proc = self._spawn(argv, fish_dir, self._fish_log)
                except Exception:
                    self._fish_state = "unavailable"
                    return False
                self._fish_owned = True
                self._fish_state = "owned"
        if self._wait_ready(self._fish_reachable_fresh, _FISH_READY_TIMEOUT_S, "fish"):
            self.fish_used()
            return True
        with self._lock:
            self._fish_state = "unavailable"
        return False

    def stop_fish(self) -> None:
        """Stop the fish server — only ever one SHIMS launched itself."""
        with self._lock:
            if not self._fish_owned:
                return
            proc = self._fish_proc
            self._fish_proc = None
            self._fish_owned = False
            self._fish_state = "stopped"
        _kill_process_tree(proc)

    def _maybe_idle_stop_fish(self) -> None:
        with self._lock:
            if not self._fish_owned or self._fish_proc is None:
                return
            if self._fish_proc.poll() is not None:
                self._fish_proc = None
                self._fish_owned = False
                self._fish_state = "stopped"
                return
            last_used = self._fish_last_used
        if last_used and (self._now() - last_used) >= self.fish_idle_threshold_s():
            self.stop_fish()

    # ------------------------------------------------------------------ #
    # Housekeeping tick + shutdown
    # ------------------------------------------------------------------ #

    def tick(self) -> None:
        """One housekeeping pass (driven every ~30 s by the backend loop)."""
        # 1. Native engine idle unload (policy lives in native_engine.budget).
        try:
            from shared.native_engine import budget as native_budget, get_engine
            native_budget.maybe_idle_unload(get_engine())
        except Exception:
            pass
        # 2. fish idle stop.
        try:
            self._maybe_idle_stop_fish()
        except Exception:
            pass
        # 3. ComfyUI suspend state machine.
        with self._lock:
            active = self._draining or self._current_job_id is not None
            new_external_activity = self._activity_seq > self._drain_seq
            if active and new_external_activity and not self._suspend_requested:
                self._stop_dequeue.set()
                self._suspend_requested = True
                self._suspend_since = self._now()
            timed_out = (
                self._suspend_requested and self._current_job_id is not None
                and (self._now() - self._suspend_since) >= self.drain_timeout_s()
            )
            ready_to_suspend = self._suspend_requested and self._current_job_id is None and not self._draining
        if timed_out or ready_to_suspend:
            self._suspend_comfyui()
        # 4. Drain the queue during downtime.
        with self._lock:
            want_drain = (
                bool(self._pending) and not self._draining
                and self._current_job_id is None and not self._suspend_requested
            )
        if want_drain and self.is_idle() and self.can_afford("comfyui"):
            if self.ensure_comfyui():
                self._start_drain()

    def shutdown(self) -> None:
        """Cleanly stop everything SHIMS owns (backend lifespan exit)."""
        self._stop_dequeue.set()
        thread = self._drain_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=5.0)
        self._suspend_comfyui()
        self.stop_fish()
        for log in (self._comfy_log, self._fish_log):
            try:
                if log is not None:
                    log.close()
            except Exception:
                pass


_ORCH: ComputeOrchestrator | None = None
_ORCH_LOCK = threading.Lock()


def get_orchestrator() -> ComputeOrchestrator:
    """Process-wide ComputeOrchestrator singleton."""
    global _ORCH
    with _ORCH_LOCK:
        if _ORCH is None:
            _ORCH = ComputeOrchestrator()
        return _ORCH
