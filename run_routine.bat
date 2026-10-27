@echo off
chcp 65001 >nul
cd /d "%~dp0"
call "..\ZAFVENV\Scripts\activate"
if not exist logs mkdir logs
python -m agent.cli routine >> logs\routine_console.log 2>&1
exit /b %errorlevel%
