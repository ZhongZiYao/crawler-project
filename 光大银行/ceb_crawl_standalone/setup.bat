@echo off
REM ============================================================
REM 首次运行: 建 venv + 装依赖 + 装 Chrome (如果需要)
REM 跑完此脚本后再跑 run.bat
REM ============================================================

setlocal

REM 检查 Python
where python >nul 2>nul
if errorlevel 1 (
    echo [ERR] 找不到 python, 请先装 Python 3.9+ 并加到 PATH
    echo       下载: https://www.python.org/downloads/
    pause
    exit /b 1
)

REM 建 venv
if not exist ".venv" (
    echo [1/4] 建虚拟环境 .venv ...
    python -m venv .venv
    if errorlevel 1 (
        echo [ERR] venv 创建失败
        pause
        exit /b 1
    )
) else (
    echo [1/4] .venv 已存在
)

REM 升级 pip
echo [2/4] 升级 pip ...
.\.venv\Scripts\python.exe -m pip install --upgrade pip -q

REM 装依赖
echo [3/4] 装依赖 ...
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 (
    echo [ERR] 依赖装失败
    pause
    exit /b 1
)

REM 装 playwright chromium browser (用于检测/辅助, 实际跑用真实 Chrome)
echo [4/4] 装 playwright chromium ...
.\.venv\Scripts\python.exe -m playwright install chromium

REM 创建子目录
if not exist "download_files\产品公告\发行公告_产品说明书" mkdir "download_files\产品公告\发行公告_产品说明书"
if not exist "state" mkdir "state"
if not exist "downloads" mkdir "downloads"

echo.
echo ============================================================
echo  安装完成!
echo  1. 在你 Chrome 里访问 https://www.cebwm.com/wealth/gywm49/cpgg93/index.html
echo     过反爬 challenge (如果有), 关 Chrome
echo  2. 跑 run.bat (用 CEB_USE_CHROME_PROFILE=1 复用 cookies)
echo     或直接跑 run.bat (用独立 profile, 首次需手动过反爬)
echo ============================================================
pause
