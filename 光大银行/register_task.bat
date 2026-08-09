@echo off
REM 注册 Windows 计划任务的批处理包装
REM 解决 PowerShell 5.1 控制台 GBK 编码问题
chcp 65001 >nul
cd /d "D:\crawler-project\光大银行"
powershell -NoProfile -ExecutionPolicy Bypass -File "D:\crawler-project\光大银行\register_health_task.ps1"
pause