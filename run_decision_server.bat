@echo off
REM Start the VCU Agent Dashboard /api/decide service with the packaged environment.
set "ROOT=%~dp0"
set "PY=%ROOT%VCU_motor_code\VCU_motor_code\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
cd /d "%ROOT%vcu_agent"
"%PY%" server.py
