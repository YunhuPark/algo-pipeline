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

from src.db_factory import get_connection

from openai import OpenAI

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


def _recent_topics(days: int = 21) -> list[str]:
    """DB에서 최근 N일간 게시된 주제 목록 반환."""
    db_path = ROOT / "data" / "algo.db"
    if not db_path.exists():
        return []
    try:
        conn = get_connection(str(db_path))
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

[최우선 원칙: 최신 뉴스 기반]
★★★ 오늘 실제로 발표·출시·발생한 AI/테크 뉴스를 카드뉴스화하는 것이 최우선입니다.
     예: "OpenAI, GPT-5 출시 — 실제로 달라진 점" / "구글 Gemini 2.0 무료 전환"
     → 시의성 있는 콘텐츠는 검색·저장·공유 모두 높음

★★  뉴스 기반 + 즉시 활용: 새 기능·도구가 나왔을 때 "어떻게 쓰는지"로 연결
     예: "방금 나온 Claude 새 기능, 이렇게 쓰면 됩니다"
     → 출시 직후 콘텐츠가 알고리즘 노출 최고

★   폴백 — 특정 제품 기능 소개 (뉴스가 없거나 너무 기술적일 때만 허용)
     예: "Perplexity Pages 써봤더니" / "Notion AI 자동화 실제 사용법"
     조건: 반드시 실제 제품명이 제목에 들어가야 함. 막연한 "AI 도구 N가지" 형식 금지.

[고점수 주제 기준]
1. 뉴스성: 오늘~3일 이내 실제 발표·출시 기반 (★★★ 최중요)
2. 구체적: 기업명·제품명·수치가 제목 안에 들어감
3. YouTube 영상 존재: YouTube에 해당 제품·기능을 직접 다루는 영상이 이미 충분히 있어야 함 (★★ 중요)
   → 각 슬라이드 주제로 YouTube 검색 시 자막 있는 관련 영상이 나와야 신뢰도 높은 영상 삽입 가능
   → 너무 최신(오늘 발표)이거나 너무 niche한 주제는 영상이 없어 이미지만 나올 수 있음
   예) "OpenAI Codex 샌드박스 기능" → YouTube에 데모/튜토리얼 영상 풍부 ✅
   예) "몰타 정부 ChatGPT Plus 무료 제공" → YouTube 영상 거의 없음 ❌
4. 슬라이드 구체성: 선정한 주제로 카드 5~6장을 만들 때 각 슬라이드에 구체적인 제품명·서비스명·사건명이 반드시 들어갈 수 있어야 함
   예) "OpenAI o3 출시" → 슬라이드마다 "o3 벤치마크", "o3 가격", "o1과 차이" 등 구체 내용 가능 ✅
   예) "AI 업무 혁신" → 슬라이드가 추상적 조언만 나열됨 ❌
5. 실익: 독자가 "나한테 도움된다"고 즉시 느낌 (돈·취업·생산성)

[절대 금지]
- "AI N가지 활용법", "AI로 업무 효율 N%" 같은 막연한 리스트형 (특정 제품명 없는 것)
- "AI 윤리", "AI의 미래", "AI 시대의 도래" 등 추상적 주제
- 슬라이드 제목이 "활용 팁", "실전 적용", "효율 높이기" 같은 막연한 말로만 채워질 주제
- 뉴스 없이 GPT 일반 지식만으로 만든 주제
- YouTube 영상이 거의 없는 극히 최신 발표나 niche 이벤트 단독 주제
- 3주 내 이미 다룬 주제 반복"""


def _fetch_recent_news() -> list[str]:
    """Tavily로 최근 3일 AI/테크 뉴스 헤드라인 수집."""
    try:
        from tavily import TavilyClient
        tc = TavilyClient(api_key=os.getenv("TAVILY_API_KEY", ""))
        results = tc.search(
            "AI artificial intelligence tech news release launch",
            search_depth="basic",
            max_results=8,
            days=3,
        )
        headlines = []
        for r in results.get("results", []):
            title = r.get("title", "").strip()
            snippet = r.get("content", "")[:80].strip()
            if title:
                headlines.append(f"- {title}: {snippet}")
        return headlines[:6]
    except Exception:
        return []


def pick_topic() -> str:
    today = datetime.now().strftime("%Y년 %m월 %d일")
    recent = _recent_topics()
    avoid_block = ""
    if recent:
        avoid_block = (
            "\n\n[최근 3주 게시 주제 — 반드시 피할 것]\n"
            + "\n".join(f"- {t}" for t in recent)
        )

    # 오늘 뉴스 수집
    news_headlines = _fetch_recent_news()
    news_block = ""
    if news_headlines:
        news_block = (
            "\n\n[오늘의 최신 AI/테크 뉴스 — 이 중에서 주제를 선정하는 것을 최우선으로]\n"
            + "\n".join(news_headlines)
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
                    "인스타그램 카드뉴스로 만들기 좋은 후보 주제 3개를 골라주세요.\n"
                    "뉴스가 제공되면 반드시 그 중에서 선정하세요. 뉴스가 없거나 너무 기술적이면 특정 제품명이 들어간 주제로 대체하세요.\n"
                    "형식: 번호 없이, 한 줄에 하나씩, 각 주제 15자 이내로."
                    + news_block
                    + avoid_block
                ),
            },
        ],
        max_tokens=120,
        temperature=0.7,
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


def _already_posted_today() -> bool:
    today = datetime.now().strftime("%Y-%m-%d")
    today_prefix = datetime.now().strftime("%Y%m%d")
    db_path = ROOT / "data" / "algo.db"
    if db_path.exists():
        try:
            conn = get_connection(str(db_path))
            row = conn.execute(
                "SELECT COUNT(*) FROM posts WHERE platform='instagram' AND posted_at LIKE ?",
                (f"{today}%",)
            ).fetchone()
            conn.close()
            if row and row[0] > 0:
                return True
        except Exception:
            pass
    output_dir = ROOT / "output"
    if output_dir.exists() and list(output_dir.glob(f"{today_prefix}_*")):
        return True
    return False


def main() -> None:
    if _already_posted_today():
        print(f"[{datetime.now().strftime('%H:%M:%S')}] 오늘 이미 게시 완료 → 스킵")
        sys.exit(0)
    print("직접 GPT 주제 발행은 비활성화되었습니다. 검증된 뉴스 Queue V2를 사용합니다.")
    queued = subprocess.run(
        [sys.executable, str(ROOT / "main.py"), "--queue", "1"],
        cwd=str(ROOT),
    )
    if queued.returncode != 0:
        sys.exit(queued.returncode)
    result = subprocess.run(
        [sys.executable, str(ROOT / "main.py"), "--queue-publish", "--publish"],
        cwd=str(ROOT),
    )
    sys.exit(result.returncode)


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    main()
