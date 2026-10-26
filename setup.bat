@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist "..\ZAFVENV\Scripts\activate.bat" (
  py -3.12 -m venv "..\ZAFVENV"
  if errorlevel 1 (echo Python 3.12 not found & pause & exit /b 1)
)
call "..\ZAFVENV\Scripts\activate"
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if not exist .env copy .env.example .env
echo.
echo DONE. Virtual env: ..\ZAFVENV  (outside the project folder)
echo Now open the .env file in Notepad and fill in your values.
pause
