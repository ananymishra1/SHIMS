@echo off
REM ============================================================
REM  SHIMS — Register tray app to launch on Windows login
REM  No .exe needed: runs Python from source so SHIMS can
REM  still self-modify and the next restart picks up changes.
REM ============================================================

set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "VBS=%STARTUP%\SHIMS_Tray.vbs"

REM Resolve project root (parent of desktop_bridge\)
pushd "%~dp0.."
set "PROJECT=%CD%"
popd

set "PYTHON=%PROJECT%\.venv\Scripts\python.exe"
set "SCRIPT=%PROJECT%\desktop_bridge\tray_app.py"

if not exist "%PYTHON%" (
    echo [SHIMS] ERROR: virtual environment not found at %PYTHON%
    echo         Run setup.bat first.
    pause
    exit /b 1
)

REM Write a tiny VBScript that launches python silently (no console window)
(
    echo Set sh = CreateObject^("WScript.Shell"^)
    echo sh.Run Chr^(34^) ^& "%PYTHON%" ^& Chr^(34^) ^& " " ^& Chr^(34^) ^& "%SCRIPT%" ^& Chr^(34^), 0, False
) > "%VBS%"

echo.
echo [SHIMS] Startup entry created:
echo         %VBS%
echo.
echo [SHIMS] SHIMS tray will now launch automatically when you log in.
echo         To remove it, run remove_autostart.bat
echo.
pause
