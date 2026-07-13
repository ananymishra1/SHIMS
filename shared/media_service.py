from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any, Optional

import httpx

from .config import GENERATED_DIR, settings
from .security import new_id


def _placeholder_png(prompt: str) -> Path:
    try:
        from PIL import Image, ImageDraw
    except Exception:
        path = GENERATED_DIR / f'image_{new_id("media")}.txt'
        path.write_text(f'Image placeholder for prompt:\n{prompt}\n', encoding='utf-8')
        return path
    path = GENERATED_DIR / f'image_{new_id("media")}.png'
    image = Image.new('RGB', (1024, 768), (245, 247, 251))
    draw = ImageDraw.Draw(image)
    draw.rectangle((40, 40, 984, 728), outline=(30, 41, 59), width=4)
    draw.text((70, 80), 'SHIMS Image Placeholder', fill=(15, 23, 42))
    draw.text((70, 130), prompt[:600], fill=(30, 41, 59))
    image.save(path)
    return path


async def generate_image(prompt: str, provider: Optional[str] = None) -> dict[str, Any]:
    provider = (provider or settings.ai_provider).lower()
    if provider == 'openai' and settings.openai_api_key:
        payload = {
            'model': 'gpt-image-1',
            'prompt': prompt,
            'size': '1024x1024',
        }
        headers = {'Authorization': f'Bearer {settings.openai_api_key}', 'Content-Type': 'application/json'}
        try:
            async with httpx.AsyncClient(timeout=180) as client:
                res = await client.post('https://api.openai.com/v1/images/generations', json=payload, headers=headers)
                res.raise_for_status()
                data = res.json()
            b64 = data.get('data', [{}])[0].get('b64_json')
            if b64:
                path = GENERATED_DIR / f'image_{new_id("media")}.png'
                path.write_bytes(base64.b64decode(b64))
                return {'status': 'ok', 'provider': 'openai', 'path': str(path), 'note': 'Generated with OpenAI image adapter.'}
        except Exception as exc:
            path = _placeholder_png(prompt)
            return {'status': 'fallback', 'provider': 'placeholder', 'path': str(path), 'note': f'OpenAI image generation failed: {exc}'}
    if provider in {'comfyui', 'comfy'}:
        from .amd_acceleration import generate_comfy_image
        path = GENERATED_DIR / f'image_{new_id("media")}.png'
        result = await generate_comfy_image(prompt, output_path=path, width=1024, height=1024)
        if result.get('ok'):
            return {'status': 'ok', 'provider': 'comfyui', 'path': str(result.get('path', path)), 'note': 'Generated with local ComfyUI on AMD ROCm.'}
        path = _placeholder_png(prompt)
        return {'status': 'fallback', 'provider': 'placeholder', 'path': str(path), 'note': f'ComfyUI image generation failed: {result.get("error")}'}
    path = _placeholder_png(prompt)
    return {'status': 'fallback', 'provider': 'placeholder', 'path': str(path), 'note': 'Configure OPENAI_API_KEY or start ComfyUI for real image generation.'}


async def generate_video(prompt: str, provider: Optional[str] = None) -> dict[str, Any]:
    provider = (provider or 'gemini').lower()
    request_path = GENERATED_DIR / f'video_request_{new_id("media")}.json'
    request_path.write_text(json.dumps({'prompt': prompt, 'provider': provider, 'status': 'queued_placeholder'}, indent=2), encoding='utf-8')
    if provider in {'gemini', 'google', 'veo'} and settings.google_api_key:
        return {
            'status': 'adapter_ready',
            'provider': 'gemini-veo',
            'path': str(request_path),
            'note': 'Veo adapter credentials detected. Use docs/VIDEO_ADAPTER.md to enable polling in production.',
        }
    return {
        'status': 'placeholder',
        'provider': 'local_request_file',
        'path': str(request_path),
        'note': 'Video generation needs a cloud provider such as Gemini Veo. This file preserves the request for later generation.',
    }
