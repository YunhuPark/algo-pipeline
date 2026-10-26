$ErrorActionPreference = "Stop"
$ProjectRoot = $PSScriptRoot
Set-Location $ProjectRoot

function Test-ListeningPort([int]$Port) {
    return [bool](Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
}

New-Item -ItemType Directory -Force -Path (Join-Path $ProjectRoot "logs") | Out-Null

# 로그인 직후 몇 분간은 Windows의 애플리케이션 제어 정책(Smart App Control 등)이
# 아직 완전히 준비되지 않아, 이 시점에 실행한 python.exe가 tiktoken 등 정상
# 서명된 네이티브 DLL조차 차단하는 경우가 있다(같은 실행 체인을 몇 분 뒤 다시
# 실행하면 문제없이 통과됨을 직접 확인함). 그 초기 창을 피하기 위해 대기한다.
Start-Sleep -Seconds 60

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
