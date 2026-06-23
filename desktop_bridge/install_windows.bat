@echo off
REM SHIMS Desktop Bridge — Windows installer
REM Run this as Administrator for best results

echo ==========================================
echo  SHIMS Desktop Bridge Installer
echo ==========================================

set BRIDGE_DIR=%~dp0
set PYTHON=%BRIDGE_DIR%..\.venv\Scripts\python.exe

if not exist "%PYTHON%" (
    echo [ERROR] Python venv not found at %PYTHON%
    echo Please run the SHIMS install script first to create .venv
    pause
    exit /b 1
)

echo [1/3] Installing bridge dependencies...
"%PYTHON%" -m pip install --quiet websockets Pillow

echo [2/3] Generating secure token...
for /f "tokens=*" %%a in ('"%PYTHON%" -c "import secrets; print(secrets.token_urlsafe(32))"') do set BRIDGE_TOKEN=%%a
echo Token: %BRIDGE_TOKEN%

echo [3/3] Creating startup scripts...
(
echo @echo off
echo REM Start SHIMS Desktop Bridge
echo set SHIMS_BRIDGE_TOKEN=%BRIDGE_TOKEN%
echo cd /d "%~dp0"
echo "%~dp0..\.venv\Scripts\python.exe" bridge_server.py --host 0.0.0.0 --port 9876 --token %%SHIMS_BRIDGE_TOKEN%%
echo pause
) > "%~dp0start_bridge.bat"

echo.
echo ==========================================
echo  Installation complete!
echo  Start the bridge with: start_bridge.bat
echo  Token saved in start_bridge.bat
echo ==========================================
pause
