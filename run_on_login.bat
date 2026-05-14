@echo off
cd /d "C:\Users\박윤후\Desktop\프로젝트\cardnews"

REM ngrok + Flask 서비스가 먼저 뜰 때까지 2분 대기
echo [%date% %time%] 서비스 대기 중 (2분)... >> logs\login_trigger.log
timeout /t 120 /nobreak >nul

python scripts\run_on_login.py >> logs\login_trigger.log 2>&1
