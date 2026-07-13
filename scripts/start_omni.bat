@echo off
cd /d "%~dp0.."
start "SHIMS Omni" cmd /k ".venv\Scripts\python scripts\start_omni.py"
