@echo off
cd /d "C:\projects\cardnews"

REM 큐에 항목이 있으면 큐에서 발행, 없으면 GPT로 주제 선택해서 실행
C:\Python313\python.exe -c "
import sys, os
sys.path.insert(0, '.')
from dotenv import load_dotenv
load_dotenv()
from src.agents.content_queue import get_status
st = get_status()
sys.exit(0 if st['pending'] > 0 else 1)
" >> logs\scheduler.log 2>&1

if %errorlevel% == 0 (
    echo [%date% %time%] 큐에서 발행 >> logs\scheduler.log
    C:\Python313\python.exe main.py --queue-publish >> logs\scheduler.log 2>&1
) else (
    echo [%date% %time%] 큐 비어있음 -- GPT 주제 선택 >> logs\scheduler.log
    C:\Python313\python.exe scripts\pick_topic.py >> logs\scheduler.log 2>&1
)
