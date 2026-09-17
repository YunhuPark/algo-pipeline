$ErrorActionPreference = "Stop"
$ProjectRoot = $PSScriptRoot
Set-Location $ProjectRoot

function Resolve-ProjectPython {
    $candidates = @(
        (Join-Path $ProjectRoot "venv\Scripts\python.exe"),
        (Join-Path $ProjectRoot ".venv\Scripts\python.exe")
    )
    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) { return $candidate }
    }
    $command = Get-Command python -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    throw "Python을 찾을 수 없습니다. 프로젝트 venv를 만들거나 python을 PATH에 추가하세요."
}

function Test-ListeningPort([int]$Port) {
    return [bool](Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
}

function Start-CheckedService {
    param(
        [string]$Name,
        [int]$Port,
        [string]$Script,
        [string]$LogName
    )
    if (Test-ListeningPort $Port) {
        Write-Host "[Services] $Name already running on :$Port"
        return
    }

    Write-Host "[Services] Starting $Name..."
    $stdout = Join-Path $ProjectRoot "logs\$LogName.log"
    $stderr = Join-Path $ProjectRoot "logs\${LogName}_err.log"
    # Start-Process flattens ArgumentList; quote absolute script paths because
    # the project directory may contain spaces or Korean characters.
    $scriptArgument = '"' + $Script + '"'
    Start-Process -FilePath $script:Python `
        -ArgumentList @($scriptArgument) `
        -WorkingDirectory $ProjectRoot `
        -RedirectStandardOutput $stdout `
        -RedirectStandardError $stderr `
        -WindowStyle Hidden | Out-Null

    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        Start-Sleep -Milliseconds 500
        if (Test-ListeningPort $Port) {
            Write-Host "[Services] $Name ready on :$Port"
            return
        }
    }
    $tail = if (Test-Path $stderr) { (Get-Content $stderr -Tail 12) -join "`n" } else { "오류 로그 없음" }
    throw "$Name 시작 실패 (:${Port})`n$tail"
}

New-Item -ItemType Directory -Force -Path (Join-Path $ProjectRoot "logs") | Out-Null
$script:Python = Resolve-ProjectPython
Write-Host "[Services] Python: $script:Python"

Start-CheckedService -Name "Flask dashboard" -Port 5001 `
    -Script (Join-Path $ProjectRoot "src\dashboard\app.py") -LogName "dashboard"
Start-CheckedService -Name "Proxy router" -Port 9000 `
    -Script (Join-Path $ProjectRoot "proxy_router.py") -LogName "proxy"

Write-Host "[Services] All services ready."
Write-Host "  Dashboard: http://localhost:5001"
Write-Host "  Image proxy: http://localhost:9000"
Write-Host "  ngrok은 별도 창에서 'ngrok http 9000'으로 실행하세요."
