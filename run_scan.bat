@echo off
cd /d "%~dp0"
python audubon_scraper.py
git add data/listings.json
git commit -m "Scan %date%"
git push
