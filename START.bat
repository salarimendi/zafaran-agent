@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist "..\ZAFVENV\Scripts\activate.bat" (
  echo Virtual environment not found. Run setup.bat first.
  pause
  exit /b 1
)
call "..\ZAFVENV\Scripts\activate"

:menu
cls
echo ==================================================
echo    Zafaran Agent  -  main menu
echo ==================================================
echo.
echo  --- A. Setup / refresh site data (read-only) ---
echo   1  Check connection
echo   2  Sync site data  (categories, products, article list)
echo   3  Export all articles to Markdown
echo.
echo  --- B. Daily work (files in the inbox folder) ---
echo   4  PREVIEW products + articles (dry run, sends nothing)
echo   5  SEND to the site (new = draft, update only if update: true)
echo   6  Create new categories/tags (from knowledge\new_terms.yaml)
echo.
echo  --- C. Analysis (needs step 3 first) ---
echo   7  SEO audit  (report in reports folder)
echo   8  Style pack (for learning your writing tone)
echo.
echo  --- D. Fix existing articles (always preview + confirmation, backups first) ---
echo  10  Make internal-link plan   (then review proposals\links_*.csv)
echo  11  Apply approved links
echo  12  SEO to-do list + prompt for a chat model
echo  13  Apply SEO answer (proposals\seo_answer.csv)
echo.
echo   9  Diagnose connection problems
echo  14  OPEN THE WEB PANEL (review/approve in the browser instead of Excel)
echo  --- E. Automation ---
echo  15  Run the read-only routine now (check, sync, export, audit, report)
echo  16  Install weekly schedule on this PC (edit day/time in schedule_install.ps1)
echo  17  Remove the schedule
echo.
echo  --- F. AI (needs LLM_* settings in .env) ---
echo  18  Test the AI connection (a few tokens)
echo  19  AI: TRIAL - SEO for only 3 articles (cheapest way to check quality)
echo  20  AI: fill SEO keyword/title/meta for ALL weak articles
echo  21  AI: judge link proposals (scores 1-5)
echo.
echo   0  Exit
echo.
set "c="
set /p c=Choose a number: 
if "%c%"=="1" python -m agent.cli check
if "%c%"=="2" python -m agent.cli sync
if "%c%"=="3" python -m agent.cli export
if "%c%"=="4" (python -m agent.cli products & echo. & python -m agent.cli articles)
if "%c%"=="5" (python -m agent.cli products --go & echo. & python -m agent.cli articles --go)
if "%c%"=="6" python -m agent.cli newterms --go
if "%c%"=="7" python -m agent.cli audit
if "%c%"=="8" python -m agent.cli style
if "%c%"=="9" python -m agent.cli diagnose
if "%c%"=="10" python -m agent.cli linkplan
if "%c%"=="11" python -m agent.cli applylinks --go
if "%c%"=="12" python -m agent.cli seotodo
if "%c%"=="13" python -m agent.cli applyseo --go
if "%c%"=="14" python -m agent.cli panel
if "%c%"=="15" python -m agent.cli routine
if "%c%"=="16" powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0schedule_install.ps1"
if "%c%"=="17" powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0schedule_remove.ps1"
if "%c%"=="18" python -m agent.cli aitest
if "%c%"=="19" python -m agent.cli aiseo --go --limit 3
if "%c%"=="20" python -m agent.cli aiseo --go
if "%c%"=="21" python -m agent.cli aijudge --go
if "%c%"=="0" exit /b 0
echo.
pause
goto menu
