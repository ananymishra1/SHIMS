#!/usr/bin/env python3
"""
Start a minimal OpenAI-compatible chat server for local transformers models.

Usage:
    .venv/Scripts/python scripts/start_hf_server.py --model google/gemma-4-12B-it --load-in-4bit
    .venv/Scripts/python scripts/start_hf_server.py --model zai-org/GLM-5.2 --port 8081 --load-in-4bit
    .venv/Scripts/python scripts/start_hf_server.py --model /path/to/local/model

Then point SHIMS at it:
    HUGGINGFACE_BASE_URL=http://127.0.0.1:8080
    HUGGINGFACE_MODEL=google/gemma-4-12B-it
    HUGGINGFACE_API_KEY= (leave blank)

Supports:
- GET /v1/models
- POST /v1/chat/completions (non-streaming and SSE streaming)

Requires: transformers, torch, accelerate (for large models), fastapi, uvicorn
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, AsyncGenerator

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))


def _find_python() -> Path:
    candidates = [
        ROOT_DIR / ".venv" / "Scripts" / "python.exe",
        ROOT_DIR / ".venv" / "bin" / "python",
        Path(sys.executable),
    ]
    for c in candidates:
        if c.exists():
            return c.resolve()
    return Path("python")


def _load_model(model_id: str, device: str | None, load_in_8bit: bool, load_in_4bit: bool):
    """Load a transformers text-generation pipeline."""
    try:
        from transformers import AutoTokenizer, pipeline
        import torch
    except ImportError as exc:
        raise SystemExit(
            "Missing dependencies. Install with:\n"
            f"  {_find_python()} -m pip install transformers torch accelerate"
        ) from exc

    kwargs: dict[str, Any] = {
        "torch_dtype": torch.float16 if torch.cuda.is_available() else torch.float32,
        "device_map": "auto" if device is None else device,
    }
    if load_in_4bit:
        kwargs["load_in_4bit"] = True
    elif load_in_8bit:
        kwargs["load_in_8bit"] = True

    print(f"[hf-server] Loading {model_id} ...")
    start = time.time()
    tokenizer = AutoTokenizer.from_pretrained(model_id, trust_remote_code=True)
    pipe = pipeline(
        "text-generation",
        model=model_id,
        tokenizer=tokenizer,
        trust_remote_code=True,
        **kwargs,
    )
    print(f"[hf-server] Loaded in {time.time() - start:.1f}s")
    return pipe, tokenizer


def _extract_text(output: Any) -> str:
    """Extract generated text from pipeline output."""
    if isinstance(output, list):
        output = output[0]
    if isinstance(output, dict):
        generated = output.get("generated_text", "")
        # Chat-templated pipelines may return the full message list.
        if isinstance(generated, list):
            for msg in reversed(generated):
                if isinstance(msg, dict) and msg.get("role") == "assistant":
                    return msg.get("content", "")
            return ""
        return generated
    return str(output)


def _strip_prompt(text: str, messages: list[dict[str, str]], tokenizer: Any) -> str:
    """Remove the echoed prompt if the pipeline returned full text."""
    # Some pipelines return full text including the prompt; try to strip it.
    try:
        prompt_text = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        if text.startswith(prompt_text):
            return text[len(prompt_text):].strip()
    except Exception:
        pass
    # Fallback: remove the last user message text if it appears at the start
    for msg in reversed(messages):
        if msg.get("role") == "user" and msg.get("content"):
            content = msg["content"]
            if text.startswith(content):
                return text[len(content):].strip()
            break
    return text.strip()


def build_app(pipe: Any, tokenizer: Any, model_id: str):
    app = FastAPI(title="SHIMS Local Transformers Server")

    @app.get("/v1/models")
    async def list_models():
        return {
            "object": "list",
            "data": [
                {
                    "id": model_id,
                    "object": "model",
                    "owned_by": "local",
                }
            ],
        }

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request):
        body = await request.json()
        messages = body.get("messages", [])
        stream = body.get("stream", False)
        max_new_tokens = body.get("max_tokens", 1024)
        temperature = body.get("temperature", 0.7)
        top_p = body.get("top_p", 0.9)

        if stream:
            async def _stream() -> AsyncGenerator[str, None]:
                # Minimal streaming: generate all at once, then yield word-by-word.
                # Real streaming requires generate() with streamer; this keeps the API
                # compatible without extra complexity.
                text = _generate_once(messages, max_new_tokens, temperature, top_p)
                id_ = f"chatcmpl-{int(time.time() * 1000)}"
                # Yield the first chunk with role
                yield f"data: {json.dumps({'id': id_, 'object': 'chat.completion.chunk', 'choices': [{'index': 0, 'delta': {'role': 'assistant'}, 'finish_reason': None}]})}\n\n"
                words = re.split(r"(\s+)", text)
                for word in words:
                    yield f"data: {json.dumps({'id': id_, 'object': 'chat.completion.chunk', 'choices': [{'index': 0, 'delta': {'content': word}, 'finish_reason': None}]})}\n\n"
                yield f"data: {json.dumps({'id': id_, 'object': 'chat.completion.chunk', 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]})}\n\n"
                yield "data: [DONE]\n\n"

            return StreamingResponse(_stream(), media_type="text/event-stream")

        text = _generate_once(messages, max_new_tokens, temperature, top_p)
        return JSONResponse({
            "id": f"chatcmpl-{int(time.time() * 1000)}",
            "object": "chat.completion",
            "model": model_id,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": "stop",
                }
            ],
        })

    def _generate_once(messages, max_new_tokens, temperature, top_p) -> str:
        start = time.time()
        outputs = pipe(
            messages,
            max_new_tokens=max_new_tokens,
            temperature=temperature,
            top_p=top_p,
            do_sample=temperature > 0,
            return_full_text=True,
        )
        text = _extract_text(outputs)
        text = _strip_prompt(text, messages, tokenizer)
        print(f"[hf-server] generated {len(text)} chars in {time.time() - start:.1f}s")
        return text

    return app


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve a local transformers model via an OpenAI-compatible API")
    parser.add_argument("--model", required=True, help="HuggingFace model id or local path")
    parser.add_argument("--port", type=int, default=8080, help="Port to listen on")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind")
    parser.add_argument("--device", default=None, help="torch device_map, e.g. 'cuda:0' or 'cpu'")
    parser.add_argument("--load-in-8bit", action="store_true", help="Load model in 8-bit (saves VRAM)")
    parser.add_argument("--load-in-4bit", action="store_true", help="Load model in 4-bit (saves VRAM)")
    args = parser.parse_args()

    pipe, tokenizer = _load_model(args.model, args.device, args.load_in_8bit, args.load_in_4bit)
    app = build_app(pipe, tokenizer, args.model)

    import uvicorn
    print(f"[hf-server] Listening on http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
