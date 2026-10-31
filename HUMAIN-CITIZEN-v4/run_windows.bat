@echo off
cd /d "%~dp0"
echo Starting HUMAIN CITIZEN demo...
where python >nul 2>nul && (python server.py) || (py server.py)
pause
