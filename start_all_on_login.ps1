$ErrorActionPreference = "Stop"
$ProjectRoot = $PSScriptRoot
Set-Location $ProjectRoot

function Test-ListeningPort([int]$Port) {
    return [bool](Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
}

New-Item -ItemType Directory -Force -Path (Join-Path $ProjectRoot "logs") | Out-Null

# ── ngrok (Instagram이 카드 이미지를 가져갈 수 있는 공개 URL) ──────
if (Test-ListeningPort 4040) {
    Write-Host "[Startup] ngrok already running"
} else {
    Write-Host "[Startup] Starting ngrok..."
    $ngrokLog = Join-Path $ProjectRoot "logs\ngrok.log"
    Start-Process -FilePath "ngrok" `
        -ArgumentList @("http", "9000") `
        -WorkingDirectory $ProjectRoot `
        -RedirectStandardOutput $ngrokLog `
        -RedirectStandardError (Join-Path $ProjectRoot "logs\ngrok_err.log") `
        -WindowStyle Hidden | Out-Null
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        Start-Sleep -Milliseconds 500
        if (Test-ListeningPort 4040) {
            Write-Host "[Startup] ngrok ready"
            break
        }
    }
}

# ── 대시보드 + 이미지 프록시 ────────────────────────────────
& (Join-Path $ProjectRoot "start_services.ps1")
