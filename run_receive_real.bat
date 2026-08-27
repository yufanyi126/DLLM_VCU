@echo off
REM Run receive_demo against the configured vehicle endpoint.
set "ROOT=%~dp0"
set "PY=%ROOT%VCU_motor_code\VCU_motor_code\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
cd /d "%ROOT%VCU_motor_code\VCU_motor_code"
"%PY%" receive_demo.py
