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


# _pick_topic removed to prevent hallucination


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
        class MockResult:
            returncode = 1
        result = MockResult()
        
        if _queue_pending():
            _log("큐에서 발행")
            proc = subprocess.run(
                [sys.executable, str(ROOT / "main.py"), "--queue-publish"],
                cwd=str(ROOT),
            )
            topic = "큐 항목"
            result.returncode = proc.returncode
        else:
            _log("큐 비어있음 — 실제 뉴스 수집 후 실행")
            from src.agents.news_collector import collect_and_select
            from src.schemas.card_news import SourceLineage
            from src.pipeline import run_pipeline
            
            try:
                news = collect_and_select()
                topic = news.topic
                _log(f"선택된 주제: {topic}")
                
                source_title = news.source_items[0].title if news.source_items else topic
                source_url = news.source_items[0].url if news.source_items else ""
                
                lineage = SourceLineage(
                    topic=topic,
                    source_title=source_title,
                    source_url=source_url,
                    context=news.context,
                )
                
                res = run_pipeline(
                    topic=topic,
                    publish=True,
                    source_lineage=lineage,
                    auto=True,
                )
                
                if res and res.generation_succeeded:
                    # 게시 실패 시에도 exit code는 1로 처리하되, 실패 원인을 로그로 남김.
                    result.returncode = 0 if res.publish_succeeded else 1
                else:
                    result.returncode = 1
                    
            except Exception as e:
                _log(f"뉴스 수집/선택 실패: {e} (fail-closed)")
                result.returncode = 1
                topic = "알 수 없는 주제"
                
    finally:
        LOCK_FILE.unlink(missing_ok=True)

    _log(f"완료 (exit={result.returncode})")

    if result.returncode == 0:
        # Check meta.json for actual publish success
        import json, glob
        meta_files = sorted(glob.glob(str(ROOT / "output" / "*" / "meta.json")))
        published = False
        if meta_files:
            try:
                with open(meta_files[-1], "r", encoding="utf-8") as fm:
                    meta = json.load(fm)
                    if meta.get("ig_post_id"):
                        published = True
            except:
                pass
        
        if published:
            _notify("알고 카드뉴스 발행 완료 ✅", f"'{topic}' 카드뉴스가 인스타에 올라갔습니다.")
        else:
            _notify("알고 카드뉴스 생성 완료 ✅", f"'{topic}' 파이프라인 생성 완료 (업로드 생략/미요청).")
    else:
        _notify("알고 카드뉴스 실패 ❌", f"'{topic}' 파이프라인/게시 오류 (exit={result.returncode}). 로그 확인 필요.")

    sys.exit(result.returncode)


if __name__ == "__main__":
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8" and hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    if sys.stderr.encoding and sys.stderr.encoding.lower() != "utf-8" and hasattr(sys.stderr, "buffer"):
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    main()
