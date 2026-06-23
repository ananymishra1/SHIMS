@echo off
REM ============================================================
REM  SHIMS Desktop Tray — always-on coworker
REM  Press Ctrl+Space anywhere to open the floating chat window.
REM  Right-click the system tray icon for options.
REM ============================================================
cd /d "%~dp0.."

if not exist .venv\Scripts\python.exe (
  echo [SHIMS Tray] Virtual environment not found. Run setup.bat first.
  pause
  exit /b 1
)

echo [SHIMS Tray] Starting desktop coworker...
echo [SHIMS Tray] Press Ctrl+Space anywhere to open chat.
echo [SHIMS Tray] Look for the SHIMS icon in your system tray.
echo.

.venv\Scripts\python.exe desktop_bridge\tray_app.py %*

if errorlevel 1 (
  echo.
  echo [SHIMS Tray] Exited with error. Check messages above.
  pause
)
