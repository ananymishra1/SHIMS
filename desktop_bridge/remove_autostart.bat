@echo off
REM ============================================================
REM  SHIMS — Remove tray app from Windows startup
REM ============================================================

set "VBS=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\SHIMS_Tray.vbs"

if exist "%VBS%" (
    del "%VBS%"
    echo [SHIMS] Startup entry removed.
) else (
    echo [SHIMS] No startup entry found ^(already removed^).
)
pause
