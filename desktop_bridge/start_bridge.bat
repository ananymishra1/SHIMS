@echo off
REM Start SHIMS Desktop Bridge
set SHIMS_BRIDGE_TOKEN=VDcf6ZI5YAPCeiqiEaD-un3hPIZePTI_f4cuaR8tItc
cd /d "E:\shims_final_omni_enterprise_2026\desktop_bridge"
"E:\shims_final_omni_enterprise_2026\.venv\Scripts\python.exe" bridge_server.py --host 0.0.0.0 --port 9876 --token %SHIMS_BRIDGE_TOKEN%
pause
