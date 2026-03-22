@echo off
cd /d "%~dp0"
echo ============================================================
echo  Audubon Print Monitor - Full Run
echo  %date% %time%
echo ============================================================

echo.
echo [1/1] Daily scan (active listings + disappearance detection)...
python audubon_scraper.py
if errorlevel 1 echo [!] Daily scan exited with errors - continuing

echo.
echo Committing and pushing...
git add data\
git commit -m "Scan data update"
git push

echo.
echo ============================================================
echo  Done. %date% %time%
echo ============================================================
pause
