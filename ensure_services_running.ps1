$ErrorActionPreference = "Stop"
$ProjectRoot = $PSScriptRoot
Set-Location $ProjectRoot

function Test-ListeningPort([int]$Port) {
    return [bool](Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
}

New-Item -ItemType Directory -Force -Path (Join-Path $ProjectRoot "logs") | Out-Null

# 이 스크립트는 "로그인 시 1회 실행"이 아니라 주기적으로(예: 15분마다) 반복
# 실행되도록 예약된다 — Windows Startup 폴더 항목은 절전(잠자기)에서 깨어날
# 때는 실행되지 않고 완전히 로그아웃 후 재로그인할 때만 실행되기 때문에,
# 노트북을 매일 잠자기로만 껐다 켜는 경우 그 방식으론 절대 자동 복구되지
# 않는다. 주기적 점검 방식은 절전/재로그인 어느 쪽이든 다음 점검 주기에
# 자동으로 따라잡는다.

if (Test-ListeningPort 4040) {
    Write-Host "[Health] ngrok already running"
} else {
    Write-Host "[Health] Starting ngrok..."
    Start-Process -FilePath "ngrok" `
        -ArgumentList @("http", "9000") `
        -WorkingDirectory $ProjectRoot `
        -RedirectStandardOutput (Join-Path $ProjectRoot "logs\ngrok.log") `
        -RedirectStandardError (Join-Path $ProjectRoot "logs\ngrok_err.log") `
        -WindowStyle Hidden | Out-Null
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        Start-Sleep -Milliseconds 500
        if (Test-ListeningPort 4040) {
            Write-Host "[Health] ngrok ready"
            break
        }
    }
}

& (Join-Path $ProjectRoot "start_services.ps1")
