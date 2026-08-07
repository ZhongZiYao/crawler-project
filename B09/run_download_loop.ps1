# ============================================================
# B09 广银理财 - 产品说明书分批下载调度脚本
#
# 用法:
#   .\run_download_loop.ps1                    # 默认: 每批20个, 间隔5分钟
#   .\run_download_loop.ps1 -BatchSize 30      # 每批30个
#   .\run_download_loop.ps1 -WaitMinutes 3     # 间隔3分钟
#   .\run_download_loop.ps1 -MaxRounds 50      # 最多跑50轮
# ============================================================

param(
    [int]$BatchSize = 20,
    [int]$WaitMinutes = 5,
    [int]$MaxRounds = 100,
    [double]$DelayMin = 5,
    [double]$DelayMax = 8
)

$ErrorActionPreference = "Continue"

# 路径配置
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$PythonExe = Join-Path $ScriptDir ".venv-B09\Scripts\python.exe"
$DownloadScript = Join-Path $ScriptDir "download_prod_manual.py"
$LogFile = Join-Path $ScriptDir "download_loop.log"

# 检查环境
if (-not (Test-Path $PythonExe)) {
    Write-Host "[ERROR] Python not found: $PythonExe" -ForegroundColor Red
    exit 1
}
if (-not (Test-Path $DownloadScript)) {
    Write-Host "[ERROR] Script not found: $DownloadScript" -ForegroundColor Red
    exit 1
}

function Write-Log($msg) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$ts] $msg"
    Write-Host $line
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host " B09 广银理财 - 产品说明书分批下载调度" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host " 每批数量: $BatchSize"
Write-Host " 批次间隔: ${WaitMinutes} 分钟"
Write-Host " 请求间隔: ${DelayMin}~${DelayMax} 秒"
Write-Host " 最大轮数: $MaxRounds"
Write-Host " 日志文件: $LogFile"
Write-Host "============================================================"
Write-Host ""

$totalSuccess = 0
$totalSkip = 0
$totalFail = 0
$round = 0
$consecutiveZero = 0  # 连续0下载计数

for ($round = 1; $round -le $MaxRounds; $round++) {
    Write-Log "===== 第 $round/$MaxRounds 轮开始 ====="

    # 设置管道编码为 UTF-8，确保 PowerShell 正确捕获 Python 中文输出
    $env:PYTHONIOENCODING = "utf-8"
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8

    # 运行下载脚本
    $output = & $PythonExe $DownloadScript --batch $BatchSize --delay-min $DelayMin --delay-max $DelayMax 2>&1
    $exitCode = $LASTEXITCODE

    # 输出实时显示
    $output | ForEach-Object { Write-Host "  $_" }

    # 解析结果
    $summaryLine = ($output | Where-Object { $_ -match "完成!.*成功:" }) | Select-Object -Last 1
    $success = 0
    $skip = 0
    $fail = 0

    if ($summaryLine -match "成功:\s*(\d+)") { $success = [int]$Matches[1] }
    if ($summaryLine -match "跳过:\s*(\d+)") { $skip = [int]$Matches[1] }
    if ($summaryLine -match "失败:\s*(\d+)") { $fail = [int]$Matches[1] }

    $totalSuccess += $success
    $totalSkip += $skip
    $totalFail += $fail

    # 统计磁盘文件数
    $fileCount = (Get-ChildItem -Path (Join-Path $ScriptDir "download_files\产品说明书") -Filter "*.pdf" -ErrorAction SilentlyContinue).Count

    Write-Log "第 $round 轮结果: 成功=$success, 跳过=$skip, 失败=$fail | 累计成功=$totalSuccess | 磁盘文件=$fileCount"

    # 判断是否完成
    if ($success -eq 0 -and $fail -eq 0) {
        $consecutiveZero++
        if ($consecutiveZero -ge 2) {
            Write-Log "连续 $consecutiveZero 轮无新下载，判定为全部完成!"
            Write-Host ""
            Write-Host "============================================================" -ForegroundColor Green
            Write-Host " 全部下载完成!" -ForegroundColor Green
            Write-Host " 总成功: $totalSuccess | 磁盘文件: $fileCount" -ForegroundColor Green
            Write-Host "============================================================" -ForegroundColor Green
            break
        }
        Write-Log "本轮无新下载 (连续第 $consecutiveZero 次)，再确认一轮..."
    } else {
        $consecutiveZero = 0
    }

    if ($fail -gt 10 -and $success -eq 0) {
        Write-Log "[WARNING] 本轮全部失败(失败=$fail)，可能触发风控，等待更长时间"
        $extraWait = $WaitMinutes * 3
        Write-Log "延长等待 ${extraWait} 分钟..."
        Start-Sleep -Seconds ($extraWait * 60)
        continue
    }

    # 非最后一轮，等待
    if ($round -lt $MaxRounds) {
        Write-Log "等待 ${WaitMinutes} 分钟后开始下一轮..."
        Start-Sleep -Seconds ($WaitMinutes * 60)
    }
}

if ($round -ge $MaxRounds) {
    Write-Log "已达最大轮数 $MaxRounds，停止调度"
}

Write-Host ""
Write-Log "===== 调度结束 ====="
Write-Log "总计: 成功=$totalSuccess, 跳过=$totalSkip, 失败=$totalFail, 磁盘文件=$fileCount"
