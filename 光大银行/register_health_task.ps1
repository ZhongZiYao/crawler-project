# register_health_task.ps1 - 一键注册 Windows 计划任务
#
# 作用:
#   把 health_check.ps1 注册成"开机自启"的计划任务
#   任务名: CebwmHealthCheck
#   开机后自动启动 health_check, 之后健康检查会自己每 2h 重启 run_batch
#
# 用法 (管理员 PowerShell):
#   cd D:\crawler-project\光大银行
#   powershell -ExecutionPolicy Bypass -File .\register_health_task.ps1

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$healthPs1 = Join-Path $scriptDir "health_check.ps1"

# 任务名
$TaskName = "CebwmHealthCheck"

# 确认 health_check.ps1 存在
if (-not (Test-Path $healthPs1)) {
    Write-Error "找不到 health_check.ps1: $healthPs1"
    exit 1
}

# 检查是否以管理员身份运行
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator
)
if (-not $isAdmin) {
    Write-Warning "建议用管理员身份运行本脚本, 否则可能无法注册开机任务"
    Write-Warning "  右键 PowerShell -> 以管理员身份运行"
}

# 先尝试删除旧任务
Write-Host "[1/3] 清理旧任务 (如有)..."
schtasks /Delete /TN $TaskName /F 2>$null | Out-Null

# 构造 XML (用数组逐行拼, 避免 here-string 转义陷阱)
$tempXml = Join-Path $env:TEMP ("cebwm_health_check_" + [System.Diagnostics.Process]::GetCurrentProcess().Id + ".xml")

$xmlLines = @(
    '<?xml version="1.0" encoding="UTF-16"?>'
    '<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">'
    '  <RegistrationInfo>'
    '    <Description>光大银行爬虫健康检查调度器: 开机自启, 每 2 小时检测一次网站连通性</Description>'
    '    <Author>Crawler</Author>'
    '  </RegistrationInfo>'
    '  <Triggers>'
    '    <BootTrigger>'
    '      <Delay>PT1M</Delay>'
    '      <Enabled>true</Enabled>'
    '    </BootTrigger>'
    '  </Triggers>'
    '  <Principals>'
    '    <Principal id="Author">'
    '      <UserId>S-1-5-4</UserId>'
    '      <RunLevel>LeastPrivilege</RunLevel>'
    '    </Principal>'
    '  </Principals>'
    '  <Settings>'
    '    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>'
    '    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>'
    '    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>'
    '    <AllowHardTerminate>true</AllowHardTerminate>'
    '    <StartWhenAvailable>true</StartWhenAvailable>'
    '    <RunOnlyIfNetworkAvailable>true</RunOnlyIfNetworkAvailable>'
    '    <AllowStartOnDemand>true</AllowStartOnDemand>'
    '    <Enabled>true</Enabled>'
    '    <Hidden>false</Hidden>'
    '    <RunOnlyIfIdle>false</RunOnlyIfIdle>'
    '    <WakeToRun>false</WakeToRun>'
    '    <ExecutionTimeLimit>PT5M</ExecutionTimeLimit>'
    '    <Priority>7</Priority>'
    '  </Settings>'
    '  <Actions>'
    '    <Exec>'
    '      <Command>powershell.exe</Command>'
    ('      <Arguments>-ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}"</Arguments>' -f $healthPs1)
    ('      <WorkingDirectory>{0}</WorkingDirectory>' -f $scriptDir)
    '    </Exec>'
    '  </Actions>'
    '</Task>'
)

# 写入临时 XML 文件 (UTF-16 LE BOM, schtasks 要求)
$unicodeEncoding = New-Object System.Text.UnicodeEncoding($false, $true)
[System.IO.File]::WriteAllLines($tempXml, $xmlLines, $unicodeEncoding)

Write-Host "[2/3] 创建计划任务..."
$registerOutput = schtasks /Create /TN $TaskName /XML $tempXml 2>&1
Remove-Item $tempXml -Force -ErrorAction SilentlyContinue

if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "XML 注册失败: $registerOutput" -ForegroundColor Red
    Write-Host ""
    Write-Host "尝试命令行方式注册..."
    $taskCmd = "powershell.exe"
    $taskArgs = "-ExecutionPolicy Bypass -WindowStyle Hidden -File `"$healthPs1`""
    $cmdOutput = schtasks /Create /SC ONSTART /DELAY 0001:00 /TN $TaskName `
        /TR ('"' + $taskCmd + '" "' + $taskArgs + '"') `
        /RL HIGHEST /F 2>&1

    if ($LASTEXITCODE -ne 0) {
        Write-Error "命令行注册也失败: $cmdOutput"
        Write-Host ""
        Write-Host "请尝试手动注册:"
        Write-Host "  schtasks /Create /SC ONSTART /DELAY 0001:00 /TN $TaskName /TR '$taskCmd $taskArgs' /F"
        exit 1
    }
}

Write-Host "[3/3] 验证任务..."
$queryOutput = schtasks /Query /TN $TaskName 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Host ""
    Write-Host "注册成功!" -ForegroundColor Green
    Write-Host ""
    Write-Host "任务信息:"
    Write-Host "  名称: $TaskName"
    Write-Host "  触发: 开机后 1 分钟启动"
    Write-Host "  脚本: $healthPs1"
    Write-Host ""
    Write-Host "管理命令:"
    Write-Host "  手动运行一次: schtasks /Run /TN $TaskName"
    Write-Host "  查看状态:     schtasks /Query /TN $TaskName /V /FO LIST"
    Write-Host "  暂停调度:     schtasks /Change /TN $TaskName /DISABLE"
    Write-Host "  恢复调度:     schtasks /Change /TN $TaskName /ENABLE"
    Write-Host "  取消注册:     schtasks /Delete /TN $TaskName /F"
    Write-Host "  查看日志:     Get-Content '$scriptDir\state\health_check.log' -Tail 50"
    Write-Host ""
    Write-Host "首次使用建议手动跑一次验证:"
    Write-Host "  schtasks /Run /TN $TaskName"
} else {
    Write-Error "任务注册后查询失败: $queryOutput"
    exit 1
}