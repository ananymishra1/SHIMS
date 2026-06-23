from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Dict, Any
from PIL import Image, ImageDraw
from .settings import BASE_DIR, settings
from .documents import safe_name


def media_dir() -> Path:
    out = settings.generated_dir if settings.generated_dir.is_absolute() else BASE_DIR / settings.generated_dir
    out.mkdir(parents=True, exist_ok=True)
    return out


def render_prompt_image(prompt: str, name: str = 'image') -> Path:
    path = media_dir() / f'{safe_name(name)}.png'
    img = Image.new('RGB', (1024, 1024), (7, 17, 31))
    d = ImageDraw.Draw(img)
    d.rectangle((36, 36, 988, 988), outline=(80, 227, 194), width=4)
    d.text((70, 70), 'SHIMS Omni Generated Visual', fill=(238, 246, 255))
    y = 130
    for line in [prompt[i:i+65] for i in range(0, len(prompt), 65)][:20]:
        d.text((70, y), line, fill=(160, 178, 205))
        y += 30
    img.save(path)
    return path


async def generate_image(prompt: str) -> Dict[str, Any]:
    # Provider hook can be added here. Local fallback keeps the feature usable offline.
    path = render_prompt_image(prompt, 'image_' + prompt[:40])
    return {'ok': True, 'provider': 'local-renderer', 'path': str(path), 'note': 'Configure image provider API key for true diffusion/image model generation.'}


async def generate_video(prompt: str) -> Dict[str, Any]:
    frame = render_prompt_image(prompt, 'video_frame_' + prompt[:35])
    out = media_dir() / f'video_{safe_name(prompt[:40])}.mp4'
    try:
        subprocess.run(['ffmpeg', '-y', '-loop', '1', '-i', str(frame), '-t', '5', '-vf', 'format=yuv420p', str(out)], capture_output=True, text=True, timeout=20)
        if out.exists():
            return {'ok': True, 'provider': 'local-ffmpeg', 'path': str(out), 'note': 'Local MP4 generated from Omni storyboard frame.'}
    except Exception:
        pass
    storyboard = media_dir() / f'video_storyboard_{safe_name(prompt[:40])}.txt'
    storyboard.write_text('SHIMS Omni video storyboard\n\nPrompt: ' + prompt + '\n\nInstall FFmpeg or configure a video provider for MP4 generation.', encoding='utf-8')
    return {'ok': True, 'provider': 'storyboard-fallback', 'path': str(storyboard)}
