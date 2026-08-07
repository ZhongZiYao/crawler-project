@echo off
REM ============================================================
REM 跑爬取脚本
REM ============================================================

setlocal

if not exist ".venv\Scripts\python.exe" (
    echo [ERR] 找不到 .venv, 请先跑 setup.bat
    pause
    exit /b 1
)

REM 默认用独立 profile 模式; 改这里或用 set CEB_USE_CHROME_PROFILE=1 切到系统 Chrome 模式
REM set CEB_USE_CHROME_PROFILE=1

.\.venv\Scripts\python.exe ceb_crawl.py %*

endlocal
