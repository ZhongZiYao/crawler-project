# health_check.ps1 - 健康检查调度器
#
# 职责:
#   1. 检测 cebwm.com 是否恢复正常
#   2. 正常 -> 启动 run_batch.ps1 继续爬
#   3. 异常 -> 杀掉所有爬虫进程
#
# 退出码:
#   0 = 网站正常, 已启动 run_batch.ps1
#   1 = 网站异常, 已清理
#   2 = 健康检查本身失败 (网络/DNS)

# 强制 UTF-8 (避免 GBK 把括号/方括号搞乱)
chcp 65001 | Out-Null
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$ErrorActionPreference = "Continue"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$batchPs1 = Join-Path $scriptDir "run_batch.ps1"

# 健康检查目标
$healthUrls = @(
    "https://www.cebwm.com/wealth/gywm49/cpgg93/index.html"
    "https://www.cebwm.com/wealth/grlc/index.html"
    "https://www.cebwm.com/"
)

$HealthCheckTimeoutSec = 30
$FailureHttpCodes = @(403, 429, 503, 504)
$BlockKeywords = @("访问过于频繁", "系统繁忙", "操作频繁", "稍后再试", "验证码", "Access Denied", "Forbidden")
$LogFile = Join-Path $scriptDir "state\health_check.log"
$StateDir = Join-Path $scriptDir "state"
$BatchPidFile = Join-Path $StateDir "batch_runner.pid"
$LastCheckFile = Join-Path $StateDir "last_health_check.json"

# 单引号字符串 + 函数封装, 避免双引号里的特殊字符陷阱
function Write-Log {
    param([string]$Message, [string]$Level = 'INFO')
    $ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    $line = '[' + $ts + '] [' + $Level + '] ' + $Message
    Write-Host $line
    try {
        Add-Content -Path $LogFile -Value $line -Encoding UTF8
    } catch {}
}

function Test-UrlHealth {
    param([string]$Url)

    $result = @{
        Url = $Url
        StatusCode = 0
        ContentLength = 0
        IsBlocked = $true
        BlockReason = 'unknown'
    }

    try {
        Add-Type -AssemblyName 'System.Net.Http' -ErrorAction SilentlyContinue
        $handler = New-Object System.Net.Http.HttpClientHandler
        $handler.UseCookies = $true
        $handler.AutomaticDecompression = [System.Net.DecompressionMethods]::GZip -bor [System.Net.DecompressionMethods]::Deflate
        $client = New-Object System.Net.Http.HttpClient($handler)
        $client.Timeout = [TimeSpan]::FromSeconds($HealthCheckTimeoutSec)

        # 用 Add 而非 = 避免触发某些解析器的等号特殊处理
        $ua = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36'
        $client.DefaultRequestHeaders.Add('User-Agent', $ua)
        $client.DefaultRequestHeaders.Add('Accept-Language', 'zh-CN,zh;q=0.9')

        $response = $client.GetAsync($Url).GetAwaiter().GetResult()
        $result.StatusCode = [int]$response.StatusCode
        $content = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
        $result.ContentLength = $content.Length

        if ($FailureHttpCodes -contains $result.StatusCode) {
            $result.IsBlocked = $true
            $result.BlockReason = 'HTTP ' + $result.StatusCode
            return $result
        }

        foreach ($kw in $BlockKeywords) {
            if ($content -and $content.Contains($kw)) {
                $result.IsBlocked = $true
                $result.BlockReason = 'keyword: ' + $kw
                return $result
            }
        }

        if ($content.Length -lt 1500) {
            $result.IsBlocked = $true
            $result.BlockReason = 'content too short: ' + $content.Length + ' chars'
            return $result
        }

        $result.IsBlocked = $false
        $result.BlockReason = ''
        return $result
    } catch [System.Net.Http.HttpRequestException] {
        $result.BlockReason = 'exception: ' + $_.Exception.Message
        return $result
    } catch [System.TimeoutException] {
        $result.BlockReason = 'timeout'
        return $result
    } catch {
        $result.BlockReason = 'exception: ' + $_.Exception.GetType().Name + ': ' + $_.Exception.Message
        return $result
    }
}

function Stop-AllCrawlerProcesses {
    Write-Log '正在清理所有爬虫相关进程...' 'WARN'

    $pyProcs = Get-Process python -ErrorAction SilentlyContinue | Where-Object {
        $_.CommandLine -like '*main.py*' -or $_.CommandLine -like '*ceb_crawl.py*'
    }
    foreach ($p in $pyProcs) {
        try {
            Write-Log ('杀掉 python PID=' + $p.Id)
            Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
        } catch {}
    }

    $psProcs = Get-Process powershell -ErrorAction SilentlyContinue | Where-Object {
        $_.CommandLine -like '*run_batch.ps1*' -or $_.CommandLine -like '*health_check.ps1*'
    }
    foreach ($p in $psProcs) {
        try {
            Write-Log ('杀掉 powershell PID=' + $p.Id)
            Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
        } catch {}
    }

    if (Test-Path $BatchPidFile) {
        Remove-Item $BatchPidFile -Force -ErrorAction SilentlyContinue
    }
}

function Start-BatchRunner {
    if (-not (Test-Path $batchPs1)) {
        Write-Log ('找不到 run_batch.ps1: ' + $batchPs1) 'ERROR'
        return $false
    }

    # 清理残留的紧急停止标记 (避免之前紧急退出后无法恢复)
    # 注意: 只在网站已确认恢复时清理, 不会掩盖真实问题
    $emergencyFlag = Join-Path $scriptDir 'state\emergency_stop.flag'
    $emergencyJson = Join-Path $scriptDir 'state\emergency_exit.json'
    if (Test-Path $emergencyFlag) {
        Remove-Item $emergencyFlag -Force -ErrorAction SilentlyContinue
        Write-Log '清理残留 emergency_stop.flag' 'INFO'
    }
    if (Test-Path $emergencyJson) {
        Remove-Item $emergencyJson -Force -ErrorAction SilentlyContinue
        Write-Log '清理残留 emergency_exit.json' 'INFO'
    }

    $existing = Get-Process powershell -ErrorAction SilentlyContinue | Where-Object {
        $_.CommandLine -like '*run_batch.ps1*'
    }
    if ($existing) {
        $ids = ($existing | ForEach-Object { $_.Id }) -join ','
        Write-Log ('run_batch.ps1 已经在跑 (PID=' + $ids + '), 跳过启动')
        return $true
    }

    Write-Log '网站已恢复, 启动 run_batch.ps1...' 'INFO'
    try {
        $proc = Start-Process -FilePath 'powershell.exe' `
                              -ArgumentList '-ExecutionPolicy', 'Bypass', '-File', "`"$batchPs1`"" `
                              -WorkingDirectory $scriptDir `
                              -WindowStyle Hidden `
                              -PassThru
        Set-Content -Path $BatchPidFile -Value $proc.Id -Encoding UTF8
        Write-Log ('已启动 run_batch.ps1 (PID=' + $proc.Id + ')')
        return $true
    } catch {
        Write-Log ('启动 run_batch.ps1 失败: ' + $_.Exception.Message) 'ERROR'
        return $false
    }
}

# ============ 主流程 ============
Write-Log '===== 健康检查开始 =====' 'INFO'

if (-not (Test-Path $StateDir)) {
    New-Item -ItemType Directory -Path $StateDir -Force | Out-Null
}

$healthResults = @()
foreach ($url in $healthUrls) {
    $r = Test-UrlHealth -Url $url
    $healthResults += $r
    $status = if ($r.IsBlocked) {
        'BLOCKED (' + $r.BlockReason + ')'
    } else {
        'OK (' + $r.StatusCode + ', ' + $r.ContentLength + ' chars)'
    }
    Write-Log ('  [' + $status + '] ' + $url)
}

$blockedResults = $healthResults | Where-Object { $_.IsBlocked }
$isHealthy = ($blockedResults.Count -eq 0)

# 写 JSON 结果
try {
    $jsonResult = @{
        timestamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
        isHealthy = $isHealthy
        results = $healthResults
    } | ConvertTo-Json -Depth 5 -Compress
    Set-Content -Path $LastCheckFile -Value $jsonResult -Encoding UTF8
} catch {}

if ($isHealthy) {
    Write-Log 'OK 网站正常' 'INFO'
    $started = Start-BatchRunner
    if ($started) {
        Write-Log '===== 健康检查完成 (已启动爬虫) =====' 'INFO'
        exit 0
    } else {
        Write-Log '===== 健康检查完成 (启动失败) =====' 'ERROR'
        exit 1
    }
} else {
    Write-Log 'WARN 网站被风控, 杀掉爬虫, 注册 6 小时后自动重试' 'WARN'
    Stop-AllCrawlerProcesses

    # 自动注册 6h 后重启 (需要管理员权限, 没有则提权)
    try {
        $restartTaskName = 'CebwmRestart6h'
        $mainPy = Join-Path $scriptDir 'run_batch.ps1'
        $restartTime = (Get-Date).AddHours(6)
        $timeStr = $restartTime.ToString('HH:mm')
        $dateStr = $restartTime.ToString('yyyy-MM-dd')
        $trTask = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $mainPy + '"'

        $isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
        if (-not $isAdmin) {
            # 提权重启注册
            $tmpScript = Join-Path $StateDir '_register_restart.ps1'
            $tmpContent = @"
chcp 65001 | Out-Null
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
schtasks /Delete /TN $restartTaskName /F 2>`$null | Out-Null
`$out = schtasks /Create /SC ONCE /TN $restartTaskName /TR '$trTask' /ST $timeStr /SD $dateStr /F 2>&1
if (`$LASTEXITCODE -eq 0) { Write-Host 'TASK_OK' ; exit 0 } else { Write-Host 'TASK_FAILED' ; exit 1 }
"@
            $tmpContent | Out-File -FilePath $tmpScript -Encoding UTF8
            $proc = Start-Process powershell -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File',"`"$tmpScript`"") -Verb RunAs -Wait -PassThru
            Remove-Item $tmpScript -Force -ErrorAction SilentlyContinue
            if ($proc.ExitCode -eq 0) {
                Write-Log ('已注册 6h 后重启: ' + $restartTaskName + ' -> ' + $restartTime.ToString('yyyy-MM-dd HH:mm')) 'INFO'
            } else {
                Write-Log '注册重启任务失败 (UAC 被拒绝)' 'WARN'
            }
        } else {
            schtasks /Delete /TN $restartTaskName /F 2>$null | Out-Null
            $out = schtasks /Create /SC ONCE /TN $restartTaskName /TR $trTask /ST $timeStr /SD $dateStr /F 2>&1
            if ($LASTEXITCODE -eq 0) {
                Write-Log ('已注册 6h 后重启: ' + $restartTaskName + ' -> ' + $restartTime.ToString('yyyy-MM-dd HH:mm')) 'INFO'
            } else {
                Write-Log '注册重启任务失败' 'WARN'
            }
        }
    } catch {
        Write-Log ('注册重启任务异常: ' + $_) 'WARN'
    }

    Write-Log '===== 健康检查完成 (异常, 已停止爬虫) =====' 'WARN'
    exit 1
}