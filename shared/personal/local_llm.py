"""Compatibility shim — SHIMS Personal local LLM now lives in the native engine.

This module was the original llama-cpp-python/Ollama local-LLM abstraction.
It is superseded by ``shared.native_engine`` (the SHIMS-owned embedded
runtime); the public names are kept working as thin delegations so any
lingering imports keep functioning.
"""
from __future__ import annotations

from typing import Any, Iterator

from shared.native_engine import discover_models, get_engine


class LocalLLM:
    """Unified interface for local LLM inference (native-engine backed)."""

    def __init__(self, model_path: str | None = None, n_ctx: int = 2048) -> None:
        self.model_path = model_path
        self.n_ctx = n_ctx

    def is_available(self) -> bool:
        """Check if a local model can be loaded."""
        if self.model_path:
            from pathlib import Path
            return Path(self.model_path).exists()
        return bool(discover_models())

    def load(self) -> bool:
        """Load the model into memory. Returns True on success."""
        try:
            engine = get_engine()
            if self.model_path:
                engine.ensure_loaded(self.model_path)
            elif not engine.loaded_model_id():
                engine.start()
            return True
        except Exception:
            return False

    def generate(
        self,
        prompt: str,
        max_tokens: int = 512,
        temperature: float = 0.7,
        system_prompt: str | None = None,
        stop: list[str] | None = None,
    ) -> str:
        """Generate a text completion synchronously."""
        if not self.load():
            return "{\"error\":\"No local model available\"}"
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        try:
            kw: dict[str, Any] = {"max_tokens": max_tokens, "temperature": temperature}
            if stop:
                kw["stop"] = stop
            result = get_engine().chat_raw(messages, **kw)
            return (result.get("content") or "").strip()
        except Exception as e:
            return f"{{\"error\":\"{str(e)}\"}}"

    def generate_stream(
        self,
        prompt: str,
        max_tokens: int = 512,
        temperature: float = 0.7,
        system_prompt: str | None = None,
    ) -> Iterator[str]:
        """Stream tokens as they are generated."""
        if not self.load():
            yield "{\"error\": \"No local model available\"}"
            return
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        import json
        import queue
        import threading
        deltas: queue.Queue = queue.Queue()
        done = object()

        def _run() -> None:
            try:
                get_engine().chat_stream(messages, deltas.put,
                                         max_tokens=max_tokens, temperature=temperature)
            except Exception as e:
                deltas.put(json.dumps({"error": str(e)}))
            finally:
                deltas.put(done)

        threading.Thread(target=_run, daemon=True, name="local-llm-stream").start()
        while True:
            item = deltas.get()
            if item is done:
                return
            yield item

    def unload(self) -> None:
        """Free model memory."""
        try:
            get_engine().unload()
        except Exception:
            pass


def quick_local_chat(
    prompt: str,
    system: str = "You are SHIMS, a helpful personal AI assistant. Be concise and friendly.",
    **kwargs: Any,
) -> str:
    """One-shot local chat helper."""
    llm = LocalLLM()
    return llm.generate(prompt, system_prompt=system, **kwargs)
