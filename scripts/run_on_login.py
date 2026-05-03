"""
로그인 시 실행 — 오늘 게시물이 없으면 자동 생성·업로드.

동작:
  1. DB에서 오늘 날짜 instagram 게시물 확인
  2. output/ 폴더에서 오늘 날짜 생성 결과 확인
  3. 둘 다 없으면 → 큐 우선, 큐 없으면 GPT 주제 선택 후 파이프라인 실행
  4. 이미 오늘 게시물 있으면 → 조용히 종료
  5. 성공/실패 모두 Windows 알림으로 통보
"""
from __future__ import annotations

import io
import socket
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "login_trigger.log"


def _log(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _port_open(port: int) -> bool:
    try:
        s = socket.create_connection(("localhost", port), timeout=2)
        s.close()
        return True
    except OSError:
        return False


def _ensure_services() -> None:
    """Flask(5001)·Proxy(9000)·ngrok 이 꺼져 있으면 start_services.bat 실행."""
    needs_start = not _port_open(5001) or not _port_open(9000)
    if needs_start:
        _log("서비스 미실행 감지 → start_services.bat 시작...")
        bat = ROOT / "start_services.bat"
        subprocess.Popen(
            ["cmd", "/c", str(bat)],
            cwd=str(ROOT),
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        # 서비스가 뜰 때까지 최대 60초 대기
        for i in range(60):
            time.sleep(1)
            if _port_open(5001) and _port_open(9000):
                _log(f"서비스 준비 완료 ({i+1}초 소요)")
                return
        _log("경고: 서비스 시작 60초 초과 — 계속 진행")
    else:
        _log("서비스 정상 실행 중 (5001·9000)")


def _notify(title: str, message: str) -> None:
    """Windows 토스트 알림 표시 (PowerShell 사용, 의존성 없음)."""
    ps_script = f"""
Add-Type -AssemblyName System.Windows.Forms
$n = New-Object System.Windows.Forms.NotifyIcon
$n.Icon = [System.Drawing.SystemIcons]::Information
$n.BalloonTipIcon = 'Info'
$n.BalloonTipTitle = '{title}'
$n.BalloonTipText = '{message}'
$n.Visible = $true
$n.ShowBalloonTip(8000)
Start-Sleep -Seconds 9
$n.Dispose()
"""
    try:
        subprocess.Popen(
            ["powershell", "-WindowStyle", "Hidden", "-Command", ps_script],
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    except Exception as e:
        _log(f"알림 전송 실패: {e}")


def _get_today_post_info() -> tuple[str, str]:
    """오늘 업로드된 게시물의 (주제, permalink) 반환. 없으면 ('', '')."""
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        db_path = ROOT / "data" / "algo.db"
        if db_path.exists():
            conn = sqlite3.connect(str(db_path))
            row = conn.execute(
                "SELECT topic, post_id FROM posts WHERE platform='instagram' AND posted_at LIKE ? ORDER BY id DESC LIMIT 1",
                (f"{today}%",)
            ).fetchone()
            conn.close()
            if row:
                topic, post_id = row
                return topic, post_id
    except Exception:
        pass
    return "", ""


def already_posted_today() -> bool:
    """오늘 날짜에 이미 instagram에 게시했는지 확인."""
    today = datetime.now().strftime("%Y-%m-%d")

    # 1) DB 확인
    try:
        from src.db import init_db
        init_db()
        db_path = ROOT / "data" / "algo.db"
        if db_path.exists():
            conn = sqlite3.connect(str(db_path))
            row = conn.execute(
                "SELECT COUNT(*) FROM posts WHERE platform='instagram' AND posted_at LIKE ?",
                (f"{today}%",)
            ).fetchone()
            conn.close()
            if row and row[0] > 0:
                _log(f"오늘({today}) DB에 게시물 {row[0]}건 확인 → 스킵")
                return True
    except Exception as e:
        _log(f"DB 확인 실패: {e}")

    # 2) output 폴더 확인 (YYYYMMDD_ 로 시작하는 폴더)
    today_prefix = datetime.now().strftime("%Y%m%d")
    output_dir = ROOT / "output"
    if output_dir.exists():
        matches = list(output_dir.glob(f"{today_prefix}_*"))
        if matches:
            _log(f"오늘({today_prefix}) output 폴더 확인 ({matches[0].name}) → 스킵")
            return True

    return False


def has_pending_queue() -> bool:
    """큐에 pending 항목이 있는지 확인."""
    try:
        from src.agents.content_queue import get_status
        return get_status()["pending"] > 0
    except Exception:
        return False


def main() -> None:
    _log("=== 로그인 트리거 시작 ===")
    _ensure_services()

    if already_posted_today():
        _log("오늘 이미 게시 완료 → 종료")
        return

    _log("오늘 게시물 없음 → 파이프라인 시작")
    _notify("알고 카드뉴스 🤖", "카드뉴스 생성을 시작합니다...")

    if has_pending_queue():
        _log("큐에서 발행")
        cmd = [sys.executable, str(ROOT / "main.py"), "--queue-publish", "--publish"]
    else:
        _log("GPT 주제 선택 후 생성")
        cmd = [sys.executable, str(ROOT / "scripts" / "pick_topic.py")]

    result = subprocess.run(cmd, cwd=str(ROOT))
    _log(f"파이프라인 종료 (exit={result.returncode})")

    if result.returncode == 0:
        # 업로드된 게시물 정보 조회
        topic, post_id = _get_today_post_info()
        if topic:
            _notify(
                "✅ 알고 카드뉴스 업로드 완료",
                f"주제: {topic}\nInstagram에 자동 게시됐어요!"
            )
            _log(f"알림 전송: '{topic}' 업로드 완료")
        else:
            _notify("✅ 알고 카드뉴스", "카드뉴스가 생성됐습니다.")
    else:
        _notify(
            "❌ 알고 카드뉴스 오류",
            "생성 중 오류가 발생했어요.\nlogs/login_trigger.log 확인해주세요."
        )
        _log("오류 알림 전송")


if __name__ == "__main__":
    main()
