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


def _recent_topics(days: int = 21) -> list[str]:
    import sqlite3 as _sql
    db_path = ROOT / "data" / "algo.db"
    if not db_path.exists():
        return []
    try:
        conn = _sql.connect(str(db_path))
        rows = conn.execute(
            "SELECT topic FROM posts WHERE platform='instagram' "
            "AND posted_at >= date('now', ?) ORDER BY id DESC",
            (f"-{days} days",),
        ).fetchall()
        conn.close()
        return [r[0] for r in rows if r[0]]
    except Exception:
        return []


_PICK_SYSTEM = (
    "당신은 한국 인스타그램 AI/테크 계정 @algo__kr의 콘텐츠 기획자입니다.\n"
    "MZ세대(20~30대) 팔로워의 저장·공유를 유도하는 주제를 선정합니다.\n\n"
    "[인스타그램 저장률 높은 주제 형식 — 우선 선호]\n"
    "★★★ 'N가지/N선' 형식: 'ChatGPT 꿀기능 5가지' / '무료 AI 도구 7선' / '취준생 필수 AI 3가지'\n"
    "     → 독자가 나중에 하나씩 써보려고 저장함 (저장률 최고)\n"
    "★★  'Before/After' 형식: 'GPT 쓰기 전/후 비교' / '3시간 → 30분 된 방법'\n"
    "     → 바로 따라 하고 싶어서 저장함\n"
    "★★  '즉시 써먹기' 형식: '지금 바로 쓰는 ChatGPT 프롬프트' / '복붙하면 끝나는 AI 명령어'\n"
    "     → 당장 써보려고 저장함\n\n"
    "[고점수 주제 기준]\n"
    "1. 구체적: 기업명·제품명·수치가 제목 안에 들어감\n"
    "2. 실익: 돈·취업·생산성·절약에 직결\n"
    "3. 뉴스성: 최근 2주 내 실제 출시·발표·수치 업데이트 기반\n"
    "4. 실행 가능: 독자가 읽고 나서 바로 따라 할 수 있는 것\n\n"
    "[저점수 주제 — 피할 것]\n"
    "- AI 윤리·규제·사회적 영향 등 추상적 논의\n"
    "- '~의 미래', '~시대의 도래' 같이 언제나 맞는 말\n"
    "- 독자가 당장 행동할 수 없는 거시 전망\n"
    "- 뉴스 설명만 하고 끝나는 주제 (기사 요약 ≠ 카드뉴스)"
)


def _pick_topic() -> str:
    from openai import OpenAI
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    today = datetime.now().strftime("%Y년 %m월 %d일")
    recent = _recent_topics()
    avoid_block = ""
    if recent:
        avoid_block = (
            "\n\n[최근 3주 게시 주제 — 반드시 피할 것]\n"
            + "\n".join(f"- {t}" for t in recent)
        )

    # 1단계: 후보 3개 생성
    cand_resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": _PICK_SYSTEM},
            {
                "role": "user",
                "content": (
                    f"오늘은 {today}입니다.\n"
                    "최근 AI/테크 트렌드 중에서 인스타그램 카드뉴스로 만들기 좋은 후보 주제 3개를 골라주세요.\n"
                    "형식: 번호 없이, 한 줄에 하나씩, 각 주제 10자 이내로."
                    + avoid_block
                ),
            },
        ],
        max_tokens=80,
        temperature=0.95,
    )
    candidates = [
        line.strip().strip('"').strip("'").lstrip("123456789.-) ")
        for line in cand_resp.choices[0].message.content.strip().splitlines()
        if line.strip()
    ][:3]

    if len(candidates) == 1:
        return candidates[0]

    candidates_text = "\n".join(f"{i+1}. {t}" for i, t in enumerate(candidates))

    # 2단계: 기준별 평가 후 최고 선택
    pick_resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": _PICK_SYSTEM},
            {
                "role": "user",
                "content": (
                    f"다음 주제 후보 중 인스타그램 저장·공유율이 가장 높을 것으로 예상되는 것 1개를 골라주세요.\n\n"
                    f"{candidates_text}\n\n"
                    "평가 기준: 구체성(기업명·수치) > 실익(돈·취업·생산성) > 뉴스성 > 놀라움 > 실행가능성\n"
                    "선택한 주제만 그대로 출력. 설명 없이."
                ),
            },
        ],
        max_tokens=30,
        temperature=0.3,
    )
    result = pick_resp.choices[0].message.content.strip().strip('"').strip("'")
    result = result.lstrip("123456789.-) ").strip()

    if not any(result in c or c in result for c in candidates):
        return candidates[0]
    return result


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
