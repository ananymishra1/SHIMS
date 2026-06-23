@echo off
cd /d "%~dp0.."
start "SHIMS Local Factory" cmd /k ".venv\Scripts\python scripts\start_factory.py"
