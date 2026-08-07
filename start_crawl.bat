@echo off
chcp 65001 >nul
cd /d "E:\Program Files\PythonProject\crawler project\zzy_crawler"
echo [%date% %time%] Starting run_all.py ...
python -X utf8 -u run_all.py --timeout 3600
echo [%date% %time%] All done. Exit code: %ERRORLEVEL%
