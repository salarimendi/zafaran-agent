@echo off
chcp 65001 >nul
cd /d "%~dp0"
call "..\ZAFVENV\Scripts\activate"
echo ===== PRODUCTS (dry run) =====
python -m agent.cli products
echo.
echo ===== ARTICLES (dry run) =====
python -m agent.cli articles
echo.
pause
