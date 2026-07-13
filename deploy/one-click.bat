@echo off
REM ============================================================
REM  SHIMS One-Click Deploy (Windows)
REM  ----------------------------------------------------------
REM  This script turns a fresh Windows machine into a running
REM  SHIMS instance in one double-click.  It:
REM    1. Installs uv (fast Python package manager) if missing
REM    2. Installs Python 3.12 via uv
REM    3. Creates a .venv and installs all dependencies
REM    4. Installs Playwright browsers
REM    5. Creates a starter .env if one doesn't exist
REM    6. Launches SHIMS (Desktop Bridge + Omni)
REM
REM  Self-evolution is fully preserved — the app runs from the
REM  filesystem and can rewrite its own source files.
REM ============================================================
setlocal EnableDelayedExpansion

cd /d "%~dp0\.."
set "SHIMS_ROOT=%CD%"
echo [SHIMS One-Click] Root: %SHIMS_ROOT%

REM ----------------------------------------------------------
REM 1. Ensure uv is available
REM ----------------------------------------------------------
where uv >nul 2>nul
if errorlevel 1 (
    echo [SHIMS One-Click] uv not found. Installing via PowerShell ...
    powershell -ExecutionPolicy Bypass -Command "& { $url='https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip'; $tmp='$env:TEMP\uv.zip'; Invoke-WebRequest -Uri $url -OutFile $tmp; Expand-Archive -Path $tmp -DestinationPath '$env:TEMP\uv' -Force; Copy-Item '$env:TEMP\uv\uv.exe' -Destination '%LOCALAPPDATA%\Microsoft\WindowsApps\uv.exe' -Force }"
    where uv >nul 2>nul
    if errorlevel 1 (
        echo [SHIMS One-Click] ERROR: could not install uv. Please install manually from https://docs.astral.sh/uv/
        pause
        exit /b 1
    )
    echo [SHIMS One-Click] uv installed successfully.
) else (
    echo [SHIMS One-Click] uv is already available.
)

REM ----------------------------------------------------------
REM 2. Install Python 3.12 via uv (idempotent)
REM ----------------------------------------------------------
echo [SHIMS One-Click] Ensuring Python 3.12 is installed via uv ...
uv python install 3.12 --quiet
if errorlevel 1 (
    echo [SHIMS One-Click] WARNING: uv python install returned an error. Trying to continue anyway ...
)

REM ----------------------------------------------------------
REM 3. Create virtual environment
REM ----------------------------------------------------------
if not exist "%SHIMS_ROOT%\.venv\Scripts\python.exe" (
    echo [SHIMS One-Click] Creating virtual environment (.venv) ...
    uv venv --python 3.12 "%SHIMS_ROOT%\.venv"
    if not exist "%SHIMS_ROOT%\.venv\Scripts\python.exe" (
        echo [SHIMS One-Click] ERROR: failed to create .venv.
        pause
        exit /b 1
    )
) else (
    echo [SHIMS One-Click] .venv already exists.
)

REM ----------------------------------------------------------
REM 4. Install dependencies
REM ----------------------------------------------------------
echo [SHIMS One-Click] Installing Python dependencies (this may take a few minutes) ...
uv pip install -r "%SHIMS_ROOT%\requirements.txt" --python "%SHIMS_ROOT%\.venv\Scripts\python.exe"
if errorlevel 1 (
    echo [SHIMS One-Click] Some packages failed (common: webrtcvad). Retrying with wheels alternative ...
    uv pip install webrtcvad-wheels --python "%SHIMS_ROOT%\.venv\Scripts\python.exe"
)

REM ----------------------------------------------------------
REM 5. Install Playwright browsers
REM ----------------------------------------------------------
echo [SHIMS One-Click] Installing Playwright browsers ...
"%SHIMS_ROOT%\.venv\Scripts\python.exe" -m playwright install chromium

REM ----------------------------------------------------------
REM 6. Create .env starter if missing
REM ----------------------------------------------------------
if not exist "%SHIMS_ROOT%\.env" (
    echo [SHIMS One-Click] Creating starter .env file ...
    (
        echo # SHIMS Environment Configuration
        echo # Fill in your API keys below to enable cloud providers.
        echo # Local Ollama works without any cloud keys.
        echo.
        echo SHIMS_OMNI_PORT=8010
        echo SHIMS_BRIDGE_PORT=9876
        echo SHIMS_BRIDGE_TOKEN=change-me-bridge-token
        echo ENTERPRISE_BRIDGE_TOKEN=change-me-enterprise-token
        echo SHIMS_SECRET_KEY=change-me-local-secret
        echo OLLAMA_HOST=http://127.0.0.1:11434
        echo SHIMS_OLLAMA_MODEL=llama3.2:latest
        echo SHIMS_AI_PROVIDER=ollama
        echo.
        echo # --- Optional cloud providers ---
        echo # OPENAI_API_KEY=sk-...
        echo # ANTHROPIC_API_KEY=sk-ant-...
        echo # GEMINI_API_KEY=...
        echo # KIMI_API_KEY=...
        echo # DEEPSEEK_API_KEY=...
        echo # QWEN_API_KEY=...
        echo.
        echo # --- Kimi model ---
        echo # Valid names: kimi-k2.6, kimi-k2.5, moonshot-v1-128k, moonshot-v1-32k, moonshot-v1-8k
        echo # You can also type the shorthand: k2.6, k2.5, 128k, 32k, 8k
        echo KIMI_MODEL=moonshot-v1-8k
    ) > "%SHIMS_ROOT%\.env"
    echo [SHIMS One-Click] .env created. EDIT IT to add your API keys before using cloud models.
) else (
    echo [SHIMS One-Click] .env already exists. Skipping creation.
)

REM ----------------------------------------------------------
REM 7. Launch SHIMS
REM ----------------------------------------------------------
echo.
echo ============================================================
echo  SHIMS One-Click setup complete.
echo ============================================================
echo  Open your browser to:  http://localhost:8010
echo  Council of the Wise:  http://localhost:8010/omni-duobot
echo.
echo  To stop: close the terminal windows that will open.
echo  To restart: run this script again, or run START_SHIMS.bat
echo ============================================================
echo.

"%SHIMS_ROOT%\.venv\Scripts\python.exe" "%SHIMS_ROOT%\scripts\start_shims.py" --no-verify

if errorlevel 1 (
    echo.
    echo [SHIMS One-Click] Startup reported an error. Check messages above.
    pause
    exit /b 1
)

echo.
echo [SHIMS One-Click] All services launched. You can close this window.
pause
endlocal
