"""
큐가 비어있을 때 GPT로 오늘의 주제를 선택해서 파이프라인 실행.
Tavily 없이 OpenAI만으로 동작.
2단계 선정: 후보 3개 생성 → 기준별 평가 후 최고 선택
"""
from __future__ import annotations

import io
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

import sqlite3

from openai import OpenAI

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


def _recent_topics(days: int = 21) -> list[str]:
    """DB에서 최근 N일간 게시된 주제 목록 반환."""
    db_path = ROOT / "data" / "algo.db"
    if not db_path.exists():
        return []
    try:
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute(
            "SELECT topic FROM posts WHERE platform='instagram' "
            "AND posted_at >= date('now', ?) ORDER BY id DESC",
            (f"-{days} days",),
        ).fetchall()
        conn.close()
        return [r[0] for r in rows if r[0]]
    except Exception:
        return []


_SYSTEM_PROMPT = """\
당신은 한국 인스타그램 AI/테크 계정 @algo__kr의 콘텐츠 기획자입니다.
MZ세대(20~30대) 팔로워의 저장·공유를 유도하는 주제를 선정합니다.

[인스타그램 저장률 높은 주제 형식 — 우선 선호]
★★★ "N가지/N선" 형식: "ChatGPT 꿀기능 5가지" / "무료 AI 도구 7선" / "취준생 필수 AI 3가지"
     → 독자가 나중에 하나씩 써보려고 저장함 (저장률 최고)
★★  "Before/After" 형식: "GPT 쓰기 전/후 비교" / "3시간 → 30분 된 방법"
     → 바로 따라 하고 싶어서 저장함
★★  "즉시 써먹기" 형식: "지금 바로 쓰는 ChatGPT 프롬프트" / "복붙하면 끝나는 AI 명령어"
     → 당장 써보려고 저장함
★   "몰랐던 사실" 형식: "GPT 유저 90%가 모르는 기능" / "아직 무료인 AI 도구들"
     → 나만 모를까봐 저장함

[고점수 주제 기준]
1. 구체적: 기업명·제품명·수치가 제목 안에 들어감 ("AI 시대" ❌ → "ChatGPT로 월 50만 원 버는 법" ✅)
2. 실익: 돈·취업·생산성·절약에 직결 — 독자가 "나한테 도움된다"고 즉시 느낌
3. 뉴스성: 최근 2주 내 실제 출시·발표·수치 업데이트 기반 (과거 이슈 재탕 ❌)
4. 실행 가능: 독자가 읽고 나서 바로 따라 할 수 있는 것

[저점수 주제 — 피할 것]
- AI 윤리·규제·사회적 영향 등 추상적 논의
- "~의 미래", "~시대의 도래" 같이 시간이 지나도 늘 맞는 말
- 독자가 당장 행동할 수 없는 거시 전망
- 뉴스 설명만 하고 끝나는 주제 (기사 요약 ≠ 카드뉴스)"""


def pick_topic() -> str:
    today = datetime.now().strftime("%Y년 %m월 %d일")
    recent = _recent_topics()
    avoid_block = ""
    if recent:
        avoid_block = (
            "\n\n[최근 3주 게시 주제 — 반드시 피할 것]\n"
            + "\n".join(f"- {t}" for t in recent)
        )

    # 1단계: 후보 3개 생성
    candidates_resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
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
    candidates_raw = candidates_resp.choices[0].message.content.strip()
    candidates = [
        line.strip().strip('"').strip("'").lstrip("123456789.-) ")
        for line in candidates_raw.splitlines()
        if line.strip()
    ][:3]

    if len(candidates) == 1:
        return candidates[0]

    candidates_text = "\n".join(f"{i+1}. {t}" for i, t in enumerate(candidates))

    # 2단계: 기준별 평가 후 최고 선택
    pick_resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
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

    # 후보에 없는 텍스트가 반환되면 첫 번째 후보 사용
    if not any(result in c or c in result for c in candidates):
        return candidates[0]
    return result


def main() -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] GPT 주제 선택 중...")
    topic = pick_topic()
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 선택된 주제: {topic}")

    cmd = [
        sys.executable, str(ROOT / "main.py"),
        topic,
        "--publish",
    ]
    result = subprocess.run(cmd, cwd=str(ROOT))
    sys.exit(result.returncode)


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    main()
