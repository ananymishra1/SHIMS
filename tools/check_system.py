from __future__ import annotations

import importlib
import shutil
import subprocess
import sys

REQUIRED_MODULES = [
    'fastapi', 'uvicorn', 'jinja2', 'httpx', 'dotenv', 'docx', 'openpyxl',
    'reportlab', 'pptx', 'PIL', 'pytest'
]

print('SHIMS system check')
print('Python:', sys.version)
for module in REQUIRED_MODULES:
    try:
        importlib.import_module(module)
        print('[OK] module', module)
    except Exception as exc:
        print('[MISSING] module', module, '-', exc)

ollama = shutil.which('ollama')
print('[OK] ollama found:' if ollama else '[INFO] ollama not found in PATH:', ollama or 'install Ollama for local AI')
ffmpeg = shutil.which('ffmpeg')
print('[OK] ffmpeg found:' if ffmpeg else '[INFO] ffmpeg not found in PATH:', ffmpeg or 'install FFmpeg for media tools')

if ollama:
    try:
        proc = subprocess.run(['ollama', 'list'], capture_output=True, text=True, timeout=10)
        print('ollama list returncode:', proc.returncode)
        print(proc.stdout[:1000])
    except Exception as exc:
        print('[INFO] could not query ollama:', exc)
