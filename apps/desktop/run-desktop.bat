@echo off
REM SHIMS Desktop App — Dev Mode Runner (no build needed)
REM Just double-click this to launch the Electron desktop app.
REM The Electron app will auto-spawn the backend if it's not running.

cd /d "%~dp0"
set "NODE_DIR=%CD%\node"
set "PATH=%NODE_DIR%;%PATH%"

echo [SHIMS Desktop] Starting Electron app...
echo [SHIMS Desktop] This will auto-spawn the backend if needed.

npx electron . %*

if errorlevel 1 (
    echo.
    echo [SHIMS Desktop] Electron exited with an error.
    echo If you see "electron not found", run: npm install
    pause
)
