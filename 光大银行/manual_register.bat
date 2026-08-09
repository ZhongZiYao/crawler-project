@echo off
REM 手动注册脚本 - 必须右键以管理员身份运行
chcp 65001 >nul
echo ========================================
echo   手动注册计划任务 (右键 -> 以管理员身份运行)
echo ========================================
echo.

REM 检查是否管理员
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo [ERROR] 请右键以管理员身份运行此 bat!
    pause
    exit /b 1
)

echo [OK] 当前是管理员
echo.

schtasks /Delete /TN CebwmHealthCheck /F 2>nul
echo [1/3] 旧任务已清理

schtasks /Create /SC ONSTART /DELAY 0001:00 /TN CebwmHealthCheck /TR "\"D:\crawler-project\光大银行\_run_health.bat\"" /F
if %errorLevel% neq 0 (
    echo [2/3] FAILED, errorLevel=%errorLevel%
    pause
    exit /b 1
)
echo [2/3] 任务已创建

echo [3/3] 验证:
schtasks /Query /TN CebwmHealthCheck
echo.
echo ========================================
echo   下一步: schtasks /Run /TN CebwmHealthCheck
echo ========================================
pause