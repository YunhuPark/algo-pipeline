@echo off
cd /d "C:\Users\박윤후\Desktop\프로젝트\cardnews"

REM ── Flask 대시보드 (포트 5001) ──────────────────────────
netstat -ano | find ":5001" | find "LISTEN" >nul 2>&1
if not errorlevel 1 (
    echo [Services] Flask already running on :5001
) else (
    echo [Services] Starting Flask dashboard...
    start "" /B C:\Python313\python.exe src\dashboard\app.py > logs\dashboard.log 2>&1
    timeout /t 3 /nobreak > nul
    echo [Services] Flask started.
)

REM ── 프록시 라우터 (포트 9000) ───────────────────────────
netstat -ano | find ":9000" | find "LISTEN" >nul 2>&1
if not errorlevel 1 (
    echo [Services] Proxy router already running on :9000
) else (
    echo [Services] Starting proxy router...
    start "" /B C:\Python313\python.exe proxy_router.py > logs\proxy.log 2>&1
    timeout /t 3 /nobreak > nul
    echo [Services] Proxy router started.
)

REM ── ngrok (포트 9000 터널링) ────────────────────────────
tasklist /fi "imagename eq ngrok.exe" 2>nul | find /i "ngrok.exe" >nul
if not errorlevel 1 (
    echo [Services] ngrok already running, skipping.
    goto done
)

echo [Services] Starting ngrok tunnel (9000)...
start "" /B ngrok http --domain=runner-thirty-bucket.ngrok-free.dev 9000 > logs\ngrok.log 2>&1
timeout /t 4 /nobreak > nul
echo [Services] ngrok started.

:done
echo [Services] All services ready.
echo   알고 이미지: https://runner-thirty-bucket.ngrok-free.dev/output_img/...
echo   SafeKids API: https://runner-thirty-bucket.ngrok-free.dev/...
