@echo off
chcp 65001 >nul
cd /d "%~dp0"
call "..\ZAFVENV\Scripts\activate"
python -m agent.cli check
echo.
pause
