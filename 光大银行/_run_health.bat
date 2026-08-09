@echo off
REM 健康检查调度入口 (避免 schtasks TR 参数中的中文/引号问题)
chcp 65001 >nul
powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "D:\crawler-project\光大银行\health_check.ps1"