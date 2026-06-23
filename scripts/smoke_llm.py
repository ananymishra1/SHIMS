"""Launch pre-flight: verify the local LLM (Ollama) is reachable and can generate.

Run before a launch/demo to confirm the local brain is live:
    python scripts/smoke_llm.py
Exit code 0 = a model produced a response; 1 = not reachable / no models.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx

from shared.config import settings


def main() -> int:
    base = settings.ollama_base_url.rstrip("/")
    print(f"Ollama host: {base}")
    try:
        with httpx.Client(timeout=10) as c:
            tags = c.get(f"{base}/api/tags")
            tags.raise_for_status()
            models = [m.get("name") for m in tags.json().get("models", [])]
    except Exception as exc:
        print(f"  NOT reachable: {exc}")
        print("  Start it with: ollama serve   (and pull a model, e.g. ollama pull llama3.2)")
        return 1

    if not models:
        print("  Reachable, but no models installed. Try: ollama pull llama3.2")
        return 1
    print(f"  Models: {', '.join(models)}")

    model = settings.ollama_model if settings.ollama_model in models else models[0]
    print(f"Generating with '{model}' …")
    try:
        with httpx.Client(timeout=60) as c:
            r = c.post(f"{base}/api/generate", json={
                "model": model,
                "prompt": "Reply with exactly: SHIMS online.",
                "stream": False,
            })
            r.raise_for_status()
            text = (r.json().get("response") or "").strip()
    except Exception as exc:
        print(f"  Generation failed: {exc}")
        return 1
    print(f"  Response: {text[:200]}")
    print("LLM smoke test PASSED.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
