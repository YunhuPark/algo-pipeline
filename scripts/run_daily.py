"""
매일 오전 작업 스케줄러가 직접 실행하는 엔트리포인트.
큐에 항목이 있으면 큐 발행, 없으면 GPT로 주제 선택 후 파이프라인 실행.
"""
from __future__ import annotations

import io
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
import shutil

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

LOG = ROOT / "logs" / "scheduler.log"
LOCK_FILE = ROOT / "logs" / "pipeline.lock"


def _log(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _notify(title: str, body: str) -> None:
    """Windows 10/11 토스트 알림 (Task Scheduler 세션에서는 무시됨)."""
    if not shutil.which("powershell"):
        return
    ps = f"""
Add-Type -AssemblyName System.Windows.Forms | Out-Null
$n = New-Object System.Windows.Forms.NotifyIcon
$n.Icon = [System.Drawing.SystemIcons]::Information
$n.BalloonTipTitle = '{title}'
$n.BalloonTipText = '{body}'
$n.Visible = $true
$n.ShowBalloonTip(8000)
Start-Sleep -Milliseconds 9000
$n.Dispose()
"""
    try:
        subprocess.Popen(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


def _queue_pending() -> bool:
    try:
        from src.agents.content_queue import get_status
        return get_status()["pending"] > 0
    except Exception as e:
        _log(f"큐 확인 실패: {e}")
        return False


def _pick_topic() -> str:
    from openai import OpenAI
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    today = datetime.now().strftime("%Y년 %m월 %d일")
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": (
                    "당신은 한국 인스타그램 AI/테크 계정 @algo__kr의 콘텐츠 기획자입니다. "
                    "MZ세대가 흥미로워할 AI, 테크, 스타트업 관련 주제를 선정합니다."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"오늘은 {today}입니다. "
                    "최근 AI/테크 트렌드 중에서 인스타그램 카드뉴스로 만들기 좋은 주제 1개를 골라주세요. "
                    "주제만 짧게 (10자 이내) 답해주세요. 예: 'GPT-5의 충격', 'AI 에이전트 시대'"
                ),
            },
        ],
        max_tokens=30,
        temperature=0.9,
    )
    return resp.choices[0].message.content.strip().strip('"').strip("'")


def _is_pipeline_running() -> bool:
    if not LOCK_FILE.exists():
        return False
    try:
        import os
        pid = int(LOCK_FILE.read_text().strip())
        os.kill(pid, 0)
        return True
    except (ValueError, OSError):
        LOCK_FILE.unlink(missing_ok=True)
        return False


def _try_acquire_lock() -> bool:
    """락파일 원자적 획득 시도. 성공하면 True."""
    import os
    try:
        with open(LOCK_FILE, "x") as f:
            f.write(str(os.getpid()))
        return True
    except FileExistsError:
        return False


def main() -> None:
    _log("=== 알고 일일 자동화 시작 ===")

    # stale 락파일 정리
    _is_pipeline_running()

    if not _try_acquire_lock():
        _log("파이프라인 이미 실행 중 (락파일 존재) → 종료")
        return
    try:
        if _queue_pending():
            _log("큐에서 발행")
            result = subprocess.run(
                [sys.executable, str(ROOT / "main.py"), "--queue-publish"],
                cwd=str(ROOT),
            )
            topic = "큐 항목"
        else:
            _log("큐 비어있음 — GPT 주제 선택")
            topic = _pick_topic()
            _log(f"선택된 주제: {topic}")
            result = subprocess.run(
                [sys.executable, str(ROOT / "main.py"), topic, "--publish"],
                cwd=str(ROOT),
            )
    finally:
        LOCK_FILE.unlink(missing_ok=True)

    _log(f"완료 (exit={result.returncode})")

    if result.returncode == 0:
        _notify("알고 카드뉴스 발행 완료 ✅", f"'{topic}' 카드뉴스가 인스타에 올라갔습니다.")
    else:
        _notify("알고 카드뉴스 실패 ❌", f"'{topic}' 파이프라인 오류 (exit={result.returncode}). 로그 확인 필요.")

    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
