@echo off
chcp 65001 >nul
cd /d "%~dp0"
call "..\ZAFVENV\Scripts\activate"
echo ===== PRODUCTS =====
python -m agent.cli products --go
echo.
echo ===== ARTICLES =====
python -m agent.cli articles --go
echo.
pause
