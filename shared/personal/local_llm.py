"""Local LLM abstraction for SHIMS Personal AI.

Supports:
- llama.cpp Python bindings (desktop/server)
- Ollama API fallback
- Mock mode for testing
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Iterator, Optional

from shared.config import settings


class LocalLLM:
    """Unified interface for local LLM inference."""

    def __init__(self, model_path: str | None = None, n_ctx: int = 2048) -> None:
        self.model_path = model_path or self._find_default_model()
        self.n_ctx = n_ctx
        self._llama = None  # lazy import
        self._model = None
        self._ctx = None

    def _find_default_model(self) -> str | None:
        """Auto-discover a GGUF model in standard locations."""
        candidates = [
            Path.home() / ".shims" / "models",
            Path("storage/models"),
            Path("data/models"),
        ]
        for d in candidates:
            if d.exists():
                for f in sorted(d.glob("*.gguf"), key=lambda p: p.stat().st_size):
                    return str(f)
        return None

    def is_available(self) -> bool:
        """Check if a local model can be loaded."""
        return self.model_path is not None and Path(self.model_path).exists()

    def load(self) -> bool:
        """Load the model into memory. Returns True on success."""
        if not self.is_available():
            return False
        try:
            import llama_cpp  # type: ignore
            self._llama = llama_cpp
            self._model = llama_cpp.Llama(
                model_path=self.model_path,
                n_ctx=self.n_ctx,
                n_threads=max(2, os.cpu_count() or 4),
                verbose=False,
            )
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
        if self._model is None and not self.load():
            return "{\"error\":\"No local model available\"}"

        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        try:
            output = self._model.create_chat_completion(
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                stop=stop or ["<|im_end|>", "<|endoftext|>", "<|user|>"],
            )
            return output["choices"][0]["message"]["content"].strip()
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
        if self._model is None and not self.load():
            yield json.dumps({"error": "No local model available"})
            return

        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        try:
            stream = self._model.create_chat_completion(
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                stream=True,
                stop=["<|im_end|>", "<|endoftext|>", "<|user|>"],
            )
            for chunk in stream:
                delta = chunk["choices"][0]["delta"]
                if "content" in delta:
                    yield delta["content"]
        except Exception as e:
            yield json.dumps({"error": str(e)})

    def unload(self) -> None:
        """Free model memory."""
        self._model = None
        self._ctx = None


def quick_local_chat(
    prompt: str,
    system: str = "You are SHIMS, a helpful personal AI assistant. Be concise and friendly.",
    **kwargs: Any,
) -> str:
    """One-shot local chat helper."""
    llm = LocalLLM()
    return llm.generate(prompt, system_prompt=system, **kwargs)
