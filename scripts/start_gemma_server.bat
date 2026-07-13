@echo off
REM Start a local OpenAI-compatible server for google/gemma-4-12B-it.
REM Default: 4-bit quantization on port 8080.
REM Requires a GPU with enough VRAM for good performance; CPU will be very slow.

cd /d "%~dp0\.."
.venv\Scripts\python scripts\start_hf_server.py --model google/gemma-4-12B-it --port 8080 --load-in-4bit %*
