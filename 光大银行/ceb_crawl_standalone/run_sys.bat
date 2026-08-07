@echo off
setlocal
set CEB_USE_CHROME_PROFILE=1
cd /d "%~dp0"
".venv\Scripts\python.exe" ceb_crawl.py %*
endlocal
