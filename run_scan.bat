@echo off
cd /d "%~dp0"
echo ============================================================
echo  Audubon Print Monitor - Full Run
echo  %date% %time%
echo ============================================================

echo.
echo [1/3] Daily scan (active listings + disappearance detection)...
python audubon_scraper.py
if errorlevel 1 echo [!] Daily scan exited with errors - continuing

echo.
echo [2/3] eBay sold listings (all birds, all editions)...
python audubon_scraper.py --ebay-sold
if errorlevel 1 echo [!] eBay sold exited with errors - continuing

echo.
echo [3/3] LiveAuctioneers archive (per bird price results)...
python audubon_scraper.py --price-results
if errorlevel 1 echo [!] LA price results exited with errors - continuing

echo.
echo Committing and pushing...
git add data\
git commit -m "Full run %date%"
git pull --rebase
git push

echo.
echo ============================================================
echo  Done. %date% %time%
echo ============================================================
pause
