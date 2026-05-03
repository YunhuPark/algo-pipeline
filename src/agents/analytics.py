"""
Analytics — Instagram 성과 데이터 수집 + GPT-4o-mini 분석
────────────────────────────────────────────────────────
기능:
  fetch_post_insights(post_id)  — 단일 게시물 지표 수집
  sync_all_insights()           — DB 전체 posts에 대해 업데이트
  analyze_performance(persona)  — GPT-4o-mini 성과 패턴 분석
  get_best_angle()              — 최근 30일 평균 engagement 상위 앵글
  plot_performance(output_path) — matplotlib 성과 차트 PNG

환경변수 (.env):
  IG_ACCESS_TOKEN
  IG_USER_ID
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

from src.config import OPENAI_API_KEY
from src.db import insert_analytics, get_analytics, get_posts

load_dotenv()

IG_ACCESS_TOKEN: str = os.getenv("IG_ACCESS_TOKEN", "")
IG_USER_ID: str = os.getenv("IG_USER_ID", "")
IG_GRAPH_BASE = "https://graph.instagram.com"

# ── 데이터클래스 ──────────────────────────────────────────

@dataclass
class PostInsights:
    post_id: str
    likes: int = 0
    comments: int = 0
    saves: int = 0
    reach: int = 0
    impressions: int = 0


@dataclass
class PerformanceReport:
    summary: str                        # 전체 성과 요약
    best_angles: list[str] = field(default_factory=list)   # 잘 된 앵글 목록
    best_upload_times: list[str] = field(default_factory=list)  # 최적 업로드 시간
    next_directions: list[str] = field(default_factory=list)    # 다음 시도 방향
    raw_response: str = ""              # LLM 원본 응답


# ── Instagram Graph API 연동 ─────────────────────────────

def fetch_post_insights(post_id: str) -> PostInsights:
    """
    Instagram Graph API로 단일 게시물 성과 지표 수집.

    GET /{media-id}/insights?metric=likes,comments,saved,reach,impressions
    """
    if not IG_ACCESS_TOKEN:
        raise EnvironmentError("IG_ACCESS_TOKEN이 .env에 없습니다.")

    url = f"{IG_GRAPH_BASE}/{post_id}/insights"
    params = {
        "metric": "likes,comments,saved,reach,impressions",
        "access_token": IG_ACCESS_TOKEN,
    }

    result = PostInsights(post_id=post_id)

    try:
        resp = httpx.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()

        for item in data.get("data", []):
            name = item.get("name", "")
            value = item.get("values", [{}])[0].get("value", 0)

            if name == "likes":
                result.likes = int(value)
            elif name == "comments":
                result.comments = int(value)
            elif name == "saved":
                result.saves = int(value)
            elif name == "reach":
                result.reach = int(value)
            elif name == "impressions":
                result.impressions = int(value)

    except httpx.HTTPStatusError as e:
        print(f"  [Analytics] API 오류 (post_id={post_id}): {e.response.status_code}")
    except Exception as e:
        print(f"  [Analytics] 요청 실패 (post_id={post_id}): {e}")

    return result


def sync_all_insights() -> int:
    """
    DB의 모든 instagram posts 레코드에 대해 insights를 가져와 analytics 테이블에 저장.
    Returns: 업데이트된 게시물 수
    """
    posts = get_posts(platform="instagram", limit=200)
    updated = 0

    for post in posts:
        post_id = post["post_id"]
        if not post_id:
            continue

        try:
            insights = fetch_post_insights(post_id)
            insert_analytics(
                post_id=post_id,
                platform="instagram",
                likes=insights.likes,
                comments=insights.comments,
                saves=insights.saves,
                reach=insights.reach,
                impressions=insights.impressions,
            )
            updated += 1
            print(f"  [Analytics] 업데이트: {post_id} — "
                  f"좋아요 {insights.likes}, 댓글 {insights.comments}, "
                  f"저장 {insights.saves}, 도달 {insights.reach}")
        except Exception as e:
            print(f"  [Analytics] 스킵 (post_id={post_id}): {e}")

    print(f"  [Analytics] 총 {updated}개 게시물 업데이트 완료")
    return updated


# ── GPT-4o-mini 성과 분석 ─────────────────────────────────

_ANALYSIS_SYSTEM = """
당신은 인스타그램 콘텐츠 마케팅 전문가입니다.
제공된 게시물 성과 데이터를 분석하여 다음을 파악해주세요:

1. 어떤 앵글/주제가 좋아요·저장·댓글을 많이 받았는지
2. 최적 업로드 시간대 (데이터가 있을 경우)
3. 다음에 시도할 콘텐츠 방향 3가지

JSON 형식으로 응답하세요:
{
  "summary": "전체 성과 한 줄 요약",
  "best_angles": ["앵글1", "앵글2"],
  "best_upload_times": ["시간대1", "시간대2"],
  "next_directions": ["방향1", "방향2", "방향3"]
}
"""

_ANALYSIS_HUMAN = """
다음은 최근 인스타그램 게시물 성과 데이터입니다:

{data_summary}

브랜드/계정 정보: {persona_info}

위 데이터를 바탕으로 성과 패턴을 분석해주세요.
"""


def analyze_performance(persona=None) -> PerformanceReport:
    """
    GPT-4o-mini로 최근 성과 패턴 분석.

    Args:
        persona: Persona 객체 (없으면 기본값 사용)
    Returns:
        PerformanceReport
    """
    import json
    from openai import OpenAI

    rows = get_analytics(platform="instagram", limit=30)
    if not rows:
        return PerformanceReport(
            summary="분석할 데이터가 없습니다. 게시물을 업로드 후 sync_all_insights()를 실행하세요.",
        )

    # 데이터 요약 문자열 구성
    lines = []
    for r in rows:
        engagement = r["likes"] + r["comments"] * 2 + r["saves"] * 3
        lines.append(
            f"- 날짜: {str(r['posted_at'])[:10]}, "
            f"주제: {r['topic']}, 앵글: {r['angle'] or '없음'}, "
            f"좋아요: {r['likes']}, 댓글: {r['comments']}, "
            f"저장: {r['saves']}, 도달: {r['reach']}, "
            f"engagement점수: {engagement}"
        )
    data_summary = "\n".join(lines)

    persona_info = "알고 카드뉴스 계정"
    if persona is not None:
        persona_info = (
            f"{persona.brand_name} ({persona.handle}), "
            f"타깃: {persona.target_audience}"
        )

    client = OpenAI(api_key=OPENAI_API_KEY)
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        temperature=0.3,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": _ANALYSIS_SYSTEM},
            {"role": "user", "content": _ANALYSIS_HUMAN.format(
                data_summary=data_summary,
                persona_info=persona_info,
            )},
        ],
    )

    raw = response.choices[0].message.content or ""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = {}

    return PerformanceReport(
        summary=parsed.get("summary", raw[:200]),
        best_angles=parsed.get("best_angles", []),
        best_upload_times=parsed.get("best_upload_times", []),
        next_directions=parsed.get("next_directions", []),
        raw_response=raw,
    )


# ── 유틸리티 ──────────────────────────────────────────────

def get_best_angle() -> str:
    """
    최근 30일 analytics 데이터에서 평균 engagement(좋아요+댓글*2+저장*3)가
    가장 높은 앵글을 반환합니다.
    """
    rows = get_analytics(platform="instagram", limit=100)
    if not rows:
        return ""

    angle_scores: dict[str, list[int]] = {}
    for r in rows:
        angle = r["angle"] or "없음"
        score = r["likes"] + r["comments"] * 2 + r["saves"] * 3
        angle_scores.setdefault(angle, []).append(score)

    if not angle_scores:
        return ""

    best = max(
        angle_scores,
        key=lambda a: sum(angle_scores[a]) / len(angle_scores[a]),
    )
    avg = sum(angle_scores[best]) / len(angle_scores[best])
    print(f"  [Analytics] 최고 앵글: '{best}' (평균 engagement {avg:.1f})")
    return best if best != "없음" else ""


def get_performance_hints() -> str:
    """
    최근 성과 데이터를 바탕으로 콘텐츠 생성 힌트를 반환.
    content_creator.py 프롬프트에 주입해 학습된 스타일로 생성 유도.

    Returns:
        프롬프트에 삽입할 힌트 문자열 (데이터 없으면 빈 문자열)
    """
    rows = get_analytics(platform="instagram", limit=50)
    if not rows:
        return ""

    # engagement 점수 계산 (저장 3배 가중 — 알고리즘에 가장 중요)
    scored = sorted(
        rows,
        key=lambda r: r["likes"] + r["comments"] * 2 + r["saves"] * 3,
        reverse=True,
    )

    top3 = scored[:3]
    bottom3 = scored[-3:]

    lines = ["[성과 피드백 — 다음 카드뉴스 작성 시 반영]"]

    # 잘 된 게시물
    if top3:
        lines.append("\n✅ 반응 좋았던 콘텐츠 패턴:")
        for r in top3:
            eng = r["likes"] + r["comments"] * 2 + r["saves"] * 3
            hook_preview = (r.get("hook") or "")[:40]
            lines.append(
                f"  - 주제: {r['topic']} | 앵글: {r['angle'] or '없음'} | "
                f"engagement {eng} | 훅: {hook_preview}"
            )

    # 반응 없었던 게시물
    if bottom3 and len(rows) > 6:
        lines.append("\n❌ 반응 낮았던 패턴 (피할 것):")
        for r in bottom3:
            eng = r["likes"] + r["comments"] * 2 + r["saves"] * 3
            lines.append(
                f"  - 주제: {r['topic']} | 앵글: {r['angle'] or '없음'} | "
                f"engagement {eng}"
            )

    # 주제별 평균 engagement
    topic_map: dict[str, list[int]] = {}
    for r in rows:
        t = (r.get("topic") or "")[:20]
        if t:
            eng = r["likes"] + r["comments"] * 2 + r["saves"] * 3
            topic_map.setdefault(t, []).append(eng)

    if topic_map:
        best_topic = max(topic_map, key=lambda k: sum(topic_map[k]) / len(topic_map[k]))
        avg = sum(topic_map[best_topic]) / len(topic_map[best_topic])
        lines.append(f"\n📊 최고 성과 주제 유형: '{best_topic}' (평균 engagement {avg:.0f})")

    return "\n".join(lines)


def plot_performance(output_path: str | Path = "data/performance.png") -> Path:
    """
    최근 30개 게시물의 engagement 추세를 matplotlib으로 차트 PNG로 저장.

    Returns:
        저장된 파일 Path
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    from datetime import datetime as dt

    rows = get_analytics(platform="instagram", limit=30)
    if not rows:
        raise RuntimeError("분석 데이터가 없습니다.")

    # 날짜 역순(최신→오래된) → 차트용으로 정렬
    rows_sorted = sorted(rows, key=lambda r: str(r["posted_at"]))

    dates = []
    likes_list = []
    saves_list = []
    reach_list = []

    for r in rows_sorted:
        try:
            dates.append(dt.strptime(str(r["posted_at"])[:10], "%Y-%m-%d"))
        except ValueError:
            dates.append(dt.now())
        likes_list.append(r["likes"])
        saves_list.append(r["saves"])
        reach_list.append(r["reach"])

    fig, axes = plt.subplots(3, 1, figsize=(10, 8), sharex=True)
    fig.suptitle("Instagram 성과 추세", fontsize=14, fontweight="bold")

    axes[0].bar(dates, likes_list, color="#E1306C", alpha=0.8, label="좋아요")
    axes[0].set_ylabel("좋아요")
    axes[0].legend(loc="upper left")

    axes[1].bar(dates, saves_list, color="#405DE6", alpha=0.8, label="저장")
    axes[1].set_ylabel("저장")
    axes[1].legend(loc="upper left")

    axes[2].plot(dates, reach_list, color="#833AB4", marker="o", linewidth=2, label="도달")
    axes[2].set_ylabel("도달")
    axes[2].legend(loc="upper left")
    axes[2].xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))
    fig.autofmt_xdate()

    plt.tight_layout()

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)

    print(f"  [Analytics] 차트 저장: {out}")
    return out
