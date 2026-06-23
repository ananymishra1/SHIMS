@echo off
setlocal
cd /d "%~dp0.."

echo Starting SHIMS services in separate windows...
echo.

start "Ollama" cmd /k "ollama serve"
timeout /t 3 /nobreak >nul

start "SHIMS Omni" cmd /k ".venv\Scripts\python scripts\start_omni.py"
timeout /t 4 /nobreak >nul

start "SHIMS Enterprise" cmd /k ".venv\Scripts\python scripts\start_enterprise.py"
timeout /t 4 /nobreak >nul

start "SHIMS Local Factory" cmd /k ".venv\Scripts\python scripts\start_factory.py"
timeout /t 4 /nobreak >nul

start "SHIMS Desktop Bridge" cmd /k ".venv\Scripts\python desktop_bridge\bridge_server.py"
timeout /t 2 /nobreak >nul

start "SHIMS Tray" cmd /k "desktop_bridge\start_tray.bat"

echo.
echo All services launching. You can close this window.
timeout /t 5 /nobreak >nul
