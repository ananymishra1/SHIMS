@echo off
cd /d "%~dp0\..\shims_v11_reference"
if not exist shims_omni_enterprise_v11_unified_final.zip (
  echo Missing v11 zip.
  exit /b 1
)
echo Extract shims_omni_enterprise_v11_unified_final.zip to E:\shims_final_omni_enterprise_2026 first.
echo Then run this from that extracted folder:
echo   .venv\Scripts\python -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8010
echo.
echo On phone, set backend URL to http://YOUR_PC_IP:8010
pause
