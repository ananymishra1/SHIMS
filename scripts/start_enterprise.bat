@echo off
cd /d "%~dp0.."
start "SHIMS Enterprise" cmd /k ".venv\Scripts\python scripts\start_enterprise.py"
