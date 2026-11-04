@echo off
cd /d "%~dp0"
where py >nul 2>nul && (py -3 run.py --online) || (python run.py --online)
if errorlevel 1 pause
