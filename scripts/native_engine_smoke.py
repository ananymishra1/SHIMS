"""Native engine smoke test — starts the engine on the real 8B GGUF.

Prints the computed launch plan, health, then runs one streamed chat
("Say hello in five words") with ms-to-first-token and tok/s, and stops.

Usage:
    .venv/Scripts/python scripts/native_engine_smoke.py [model_path_or_id]

This loads a multi-GB model — expect the start() call to take a while.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.native_engine import get_engine  # noqa: E402


def main() -> int:
    model = sys.argv[1] if len(sys.argv) > 1 else None
    engine = get_engine()

    print("=== SHIMS native engine smoke ===")
    t0 = time.perf_counter()
    try:
        health = engine.start(model)
    except Exception as exc:
        print(f"engine failed to start: {exc}")
        return 1
    print(f"start: {time.perf_counter() - t0:.1f}s")

    print("\n-- launch plan / health --")
    for key in ("model", "model_path", "backend", "gpu_layers", "ctx", "threads",
                "port", "launch_reason"):
        print(f"  {key}: {health.get(key)}")

    print("\n-- budget --")
    from shared.native_engine import budget
    report = budget.report()
    for key in ("reserved_bytes", "weights_bytes", "kv_bytes", "headroom_bytes"):
        print(f"  {key}: {report.get(key, 0) / (1024 ** 3):.2f} GiB")

    print("\n-- streamed chat: 'Say hello in five words' --")
    first_token_at: float | None = None
    chunks: list[str] = []

    def on_delta(text: str) -> None:
        nonlocal first_token_at
        if first_token_at is None:
            first_token_at = time.perf_counter()
        chunks.append(text)
        print(text, end="", flush=True)

    t_chat = time.perf_counter()
    result = engine.chat_stream(
        [{"role": "user", "content": "Say hello in five words"}],
        on_delta,
        max_tokens=64,
    )
    t_end = time.perf_counter()
    answer = result.get("content") or "".join(chunks)

    print("\n\n-- timings --")
    if first_token_at is not None:
        print(f"  ms to first token: {(first_token_at - t_chat) * 1000:.0f}")
    gen_s = t_end - (first_token_at or t_chat)
    approx_tokens = max(1, len(answer) // 4)
    print(f"  generation: {gen_s:.2f}s for ~{approx_tokens} tokens ~= {approx_tokens / max(gen_s, 0.01):.1f} tok/s")

    engine.stop()
    print("\nengine stopped cleanly.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
