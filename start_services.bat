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

REM ── 프록시 라우터 (포트 9000, SafeKids용) ──────────────
netstat -ano | find ":9000" | find "LISTEN" >nul 2>&1
if not errorlevel 1 (
    echo [Services] Proxy router already running on :9000
) else (
    echo [Services] Starting proxy router...
    start "" /B C:\Python313\python.exe proxy_router.py > logs\proxy.log 2>&1
    timeout /t 3 /nobreak > nul
    echo [Services] Proxy router started.
)

REM ── ngrok (SafeKids API 터널링, 필요한 경우만) ──────────
REM 알고 카드뉴스 업로드는 catbox.moe를 사용하므로 ngrok 불필요.
REM SafeKids 프로젝트에 ngrok이 필요한 경우 아래 주석 해제:
REM tasklist /fi "imagename eq ngrok.exe" 2>nul | find /i "ngrok.exe" >nul
REM if not errorlevel 1 (
REM     echo [Services] ngrok already running, skipping.
REM     goto done
REM )
REM echo [Services] Starting ngrok tunnel (9000)...
REM start "" /B ngrok http --domain=runner-thirty-bucket.ngrok-free.dev 9000 > logs\ngrok.log 2>&1
REM timeout /t 4 /nobreak > nul
REM echo [Services] ngrok started.

:done
echo [Services] All services ready.
echo   Flask 대시보드: http://localhost:5001
echo   Instagram 업로드: catbox.moe (ngrok 불필요)
