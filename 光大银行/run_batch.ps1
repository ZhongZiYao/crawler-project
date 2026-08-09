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

    # 紧急退出信号 (10=被封, 11=网络异常): 立即结束本轮守护, 等 health_check 重新调度
    if ($exitCode -eq 10 -or $exitCode -eq 11) {
        Write-Host ("[{0}] ⛔ 爬虫紧急退出 (exit={1}), 判定网站异常, run_batch 守护进程退出" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $exitCode) -ForegroundColor Red
        # 创建紧急停止标记, 防止其他残留的 main.py 实例继续爬
        $flagFile = Join-Path (Split-Path -Parent $mainPy) "state\emergency_stop.flag"
        if (-not (Test-Path (Split-Path -Parent $flagFile))) {
            New-Item -ItemType Directory -Path (Split-Path -Parent $flagFile) -Force | Out-Null
        }
        "exit=$exitCode at $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" | Out-File -FilePath $flagFile -Encoding UTF8
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
