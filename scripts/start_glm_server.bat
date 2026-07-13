@echo off
REM Start a local OpenAI-compatible server for zai-org/GLM-5.2.
REM Default: 4-bit quantization on port 8081.
REM Requires a GPU with enough VRAM for good performance; CPU will be very slow.

cd /d "%~dp0\.."
.venv\Scripts\python scripts\start_hf_server.py --model zai-org/GLM-5.2 --port 8081 --load-in-4bit %*
