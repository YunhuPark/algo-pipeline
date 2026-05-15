$ProjectRoot = $PSScriptRoot
$Python = "C:\Python313\python.exe"

Set-Location $ProjectRoot

# Flask dashboard (port 5001)
$flask = netstat -ano | Select-String ":5001.*LISTEN"
if ($flask) {
    Write-Host "[Services] Flask already running on :5001"
} else {
    Write-Host "[Services] Starting Flask dashboard..."
    Start-Process -FilePath $Python `
        -ArgumentList "$ProjectRoot\src\dashboard\app.py" `
        -WorkingDirectory $ProjectRoot `
        -RedirectStandardOutput "$ProjectRoot\logs\dashboard.log" `
        -RedirectStandardError "$ProjectRoot\logs\dashboard_err.log" `
        -NoNewWindow
    Start-Sleep -Seconds 3
    Write-Host "[Services] Flask started."
}

# Proxy router (port 9000)
$proxy = netstat -ano | Select-String ":9000.*LISTEN"
if ($proxy) {
    Write-Host "[Services] Proxy router already running on :9000"
} else {
    Write-Host "[Services] Starting proxy router..."
    Start-Process -FilePath $Python `
        -ArgumentList "$ProjectRoot\proxy_router.py" `
        -WorkingDirectory $ProjectRoot `
        -RedirectStandardOutput "$ProjectRoot\logs\proxy.log" `
        -RedirectStandardError "$ProjectRoot\logs\proxy_err.log" `
        -NoNewWindow
    Start-Sleep -Seconds 3
    Write-Host "[Services] Proxy router started."
}

Write-Host "[Services] All services ready."
Write-Host "  Flask: http://localhost:5001"
