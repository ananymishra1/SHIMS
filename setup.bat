@echo off
REM ============================================================
REM  SHIMS one-time setup: create venv + install dependencies.
REM  Requires Python 3.11 (py -3.11) or a python on PATH.
REM ============================================================
setlocal
cd /d %~dp0

echo [SHIMS] Creating virtual environment (.venv) ...
if not exist .venv (
  py -3.11 -m venv .venv
  if errorlevel 1 python -m venv .venv
)
if not exist .venv\Scripts\python.exe (
  echo [SHIMS] Failed to create .venv. Install Python 3.11 and retry.
  pause
  exit /b 1
)

echo [SHIMS] Installing dependencies ...
.venv\Scripts\python.exe -m pip install --upgrade pip setuptools wheel
.venv\Scripts\python.exe -m pip install -r requirements.txt

echo [SHIMS] Verifying core imports ...
.venv\Scripts\python.exe -c "import fastapi, uvicorn, reportlab, PIL, docx; print('[SHIMS] core deps OK')"

if not exist .env (
  echo [SHIMS] No .env found - copying .env.example to .env
  copy /y .env.example .env >nul
)

echo.
echo [SHIMS] Setup complete.
echo   Next:  START_SHIMS.bat       (launches Bridge + Omni + Enterprise)
echo   Or:    .venv\Scripts\python scripts\start_shims.py
pause
