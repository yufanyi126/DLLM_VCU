@echo off
REM Run receive_demo in test mode. It does not connect to the real vehicle WebSocket.
set "ROOT=%~dp0"
set "PY=%ROOT%VCU_motor_code\VCU_motor_code\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
cd /d "%ROOT%VCU_motor_code\VCU_motor_code"
"%PY%" receive_demo.py --receive-test
