@echo off
REM ============================================================
REM  SHIMS clean master starter
REM  Starts: Desktop Bridge -> Omni
REM  Configuration is read from .env in the project root.
REM ============================================================
setlocal

cd /d "%~dp0"

if not exist .venv\Scripts\python.exe (
  echo [SHIMS] Virtual environment not found. Run setup.bat first.
  pause
  exit /b 1
)

.venv\Scripts\python.exe scripts\start_shims.py %*

if errorlevel 1 (
  echo.
  echo [SHIMS] Startup reported an error. Check the messages above.
  pause
  exit /b 1
)

echo.
echo [SHIMS] All services launched. You can close this window.
pause
