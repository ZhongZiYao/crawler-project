# Repeated batch runner for CEBWM crawler.
# Uses env vars to control batch size and sleep duration.

$env:BATCH_MAX_SUCCESS_PRODUCTS = "200"

# ============ 分工模式 ============
# 你和同事各爬一半: 你 (Worker 1) 取消下面两行的注释
$env:WORKER_INDEX = "1"
$env:WORKER_TOTAL = "2"
# 同事那边 (Worker 2) 把上面改成:
# $env:WORKER_INDEX = "2"
# $env:WORKER_TOTAL = "2"
# 如果不分工, 保持注释状态即可

# Optional overrides:
# $env:SLEEP_MINUTES = "120"   # 风控恢复期: 2 小时间隔
# $env:PYTHONUTF8 = "1"
# $env:PYTHONIOENCODING = "utf-8"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$mainPy = Join-Path $scriptDir "main.py"

# 显式使用 venv 里的 python (避免 'python was not found')
$venvPython = Join-Path (Split-Path -Parent $scriptDir) ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    # fallback: 上上级目录
    $venvPython = Join-Path (Split-Path -Parent (Split-Path -Parent $scriptDir)) ".venv\Scripts\python.exe"
}

if (-not (Test-Path $mainPy)) {
    Write-Error "main.py not found: $mainPy"
    exit 1
}

if (-not (Test-Path $venvPython)) {
    Write-Warning "venv python not found: $venvPython, fallback to PATH python"
    $venvPython = "python"
}

Write-Host ("[CONFIG] Python: $venvPython")
Write-Host ("[CONFIG] main.py: $mainPy")
Write-Host ("[CONFIG] Worker: $WORKER_INDEX / $WORKER_TOTAL")

# 启动前清理残留紧急停止标记 (避免上一轮 exit=10 创建的 flag 永久残留)
$stateDir = Join-Path (Split-Path -Parent $mainPy) "state"
$flagFile = Join-Path $stateDir "emergency_stop.flag"
$exitJson = Join-Path $stateDir "emergency_exit.json"
if (Test-Path $flagFile) {
    Remove-Item $flagFile -Force -ErrorAction SilentlyContinue
    Write-Host "[CLEANUP] 删除残留 emergency_stop.flag"
}
if (Test-Path $exitJson) {
    Remove-Item $exitJson -Force -ErrorAction SilentlyContinue
    Write-Host "[CLEANUP] 删除残留 emergency_exit.json"
}

while ($true) {
    Write-Host ("[{0}] Start batch" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"))
    & $venvPython $mainPy
    $exitCode = $LASTEXITCODE
    Write-Host ("[{0}] Batch finished (exit={1})" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $exitCode)

    # 紧急退出信号 (10=被封, 11=网络异常): 立即结束本轮守护, 自动调度 6 小时后重试
    if ($exitCode -eq 10 -or $exitCode -eq 11) {
        Write-Host ("[{0}] ⛔ 爬虫紧急退出 (exit={1}), 判定网站风控" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $exitCode) -ForegroundColor Red

        # 创建紧急停止标记, 防止其他残留的 main.py 实例继续爬
        $flagFile = Join-Path (Split-Path -Parent $mainPy) "state\emergency_stop.flag"
        if (-not (Test-Path (Split-Path -Parent $flagFile))) {
            New-Item -ItemType Directory -Path (Split-Path -Parent $flagFile) -Force | Out-Null
        }
        "exit=$exitCode at $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" | Out-File -FilePath $flagFile -Encoding UTF8

        # 自动注册 6 小时后重启任务 (需要管理员权限, 如果当前不是则提权)
        Write-Host "⏰ [AUTO_RETRY] 注册 6 小时后自动重启任务..." -ForegroundColor Yellow
        try {
            $restartTaskName = "CebwmRestart6h"
            $restartTime = (Get-Date).AddHours(6)
            $timeStr = $restartTime.ToString("HH:mm")
            $dateStr = $restartTime.ToString("yyyy-MM-dd")
            $trTask = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $mainPy + '"'

            # 检查管理员权限, 没有则提权重跑
            $isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
            if (-not $isAdmin) {
                Write-Host "   需要管理员权限, 弹 UAC 提示..." -ForegroundColor Yellow
                # 写一个临时脚本, 提权后注册任务
                $tmpScript = Join-Path (Split-Path -Parent $mainPy) "state\_register_restart.ps1"
                $tmpContent = @"
chcp 65001 | Out-Null
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
schtasks /Delete /TN $restartTaskName /F 2>`$null | Out-Null
`$out = schtasks /Create /SC ONCE /TN $restartTaskName /TR '$trTask' /ST $timeStr /SD $dateStr /F 2>&1
if (`$LASTEXITCODE -eq 0) { Write-Host "TASK_OK" ; exit 0 } else { Write-Host "TASK_FAILED: `$out" ; exit 1 }
"@
                $tmpContent | Out-File -FilePath $tmpScript -Encoding UTF8
                # 提权执行
                $proc = Start-Process powershell -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File',"`"$tmpScript`"") -Verb RunAs -Wait -PassThru
                Remove-Item $tmpScript -Force -ErrorAction SilentlyContinue
                if ($proc.ExitCode -eq 0) {
                    Write-Host ("   ✅ 已注册 6h 后重启: $restartTaskName -> {0:yyyy-MM-dd HH:mm}" -f $restartTime) -ForegroundColor Green
                } else {
                    Write-Host "   ⚠️ 注册失败 (UAC 被拒绝或出错)" -ForegroundColor Red
                }
            } else {
                # 已经是管理员, 直接注册
                schtasks /Delete /TN $restartTaskName /F 2>$null | Out-Null
                $out = schtasks /Create /SC ONCE /TN $restartTaskName /TR $trTask /ST $timeStr /SD $dateStr /F 2>&1
                if ($LASTEXITCODE -eq 0) {
                    Write-Host ("   ✅ 已注册 6h 后重启: $restartTaskName -> {0:yyyy-MM-dd HH:mm}" -f $restartTime) -ForegroundColor Green
                } else {
                    Write-Host ("   ⚠️ 注册失败: $out") -ForegroundColor Red
                }
            }
        } catch {
            Write-Host ("   ⚠️ 注册异常: $_") -ForegroundColor Red
        }

        exit $exitCode
    }

    $sleepMinutes = 5
    if ($env:SLEEP_MINUTES) {
        try {
            $sleepMinutes = [int]$env:SLEEP_MINUTES
        } catch {
            $sleepMinutes = 5
        }
    }

    if ($sleepMinutes -le 0) {
        $sleepMinutes = 5
    }

    Write-Host ("[{0}] Sleep {1} minutes" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $sleepMinutes)
    Start-Sleep -Seconds ($sleepMinutes * 60)
}
