# Repeated batch runner for CEBWM crawler.
# Uses env vars to control batch size and sleep duration.

$env:BATCH_MAX_SUCCESS_PRODUCTS = "20"

# Optional overrides:
# $env:SLEEP_MINUTES = "5"
# $env:PYTHONUTF8 = "1"
# $env:PYTHONIOENCODING = "utf-8"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$mainPy = Join-Path $scriptDir "main.py"

if (-not (Test-Path $mainPy)) {
    Write-Error "main.py not found: $mainPy"
    exit 1
}

while ($true) {
    Write-Host ("[{0}] Start batch" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"))
    & python $mainPy
    $exitCode = $LASTEXITCODE
    Write-Host ("[{0}] Batch finished (exit={1})" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $exitCode)

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
