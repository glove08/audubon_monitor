@echo off
cd /d "%~dp0"
python audubon_scraper.py --quick
git add data/listings.json
git commit -m "Quick scan %date%"
git push
