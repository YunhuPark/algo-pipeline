"""
CompetitorAnalyzer — 경쟁 계정 콘텐츠 분석
────────────────────────────────────────────────────────
Tavily로 경쟁 계정의 최신 콘텐츠를 수집하고
GPT-4o가 패턴을 분석해 차별화 전략을 제안한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel
from tavily import TavilyClient

from src.config import OPENAI_API_KEY, TAVILY_API_KEY
from src.db import insert_competitor, get_competitors

DEFAULT_COMPETITORS = [
    "뉴닉",
    "어피티",
    "캐릿",
    "1boon",
    "모닝브리핑",
]


# ── 데이터 클래스 ─────────────────────────────────────────

@dataclass
class CompetitorPost:
    account: str
    topic: str
    angle: str
    estimated_engagement: str
    url: str
    note: str


@dataclass
class CompetitorReport:
    top_topics: list[str] = field(default_factory=list)
    top_angles: list[str] = field(default_factory=list)
    gap_opportunities: list[str] = field(default_factory=list)
    recommendations: str = ""


# ── Pydantic 스키마 ───────────────────────────────────────

class _PostInfo(BaseModel):
    topic: str
    angle: str
    estimated_engagement: str
    note: str


class _PostList(BaseModel):
    posts: list[_PostInfo]


class _StrategyReport(BaseModel):
    top_topics: list[str]
    top_angles: list[str]
    gap_opportunities: list[str]
    recommendations: str


# ── 크롤링 ────────────────────────────────────────────────

def crawl_competitor(account: str) -> list[CompetitorPost]:
    if not TAVILY_API_KEY:
        print(f"  [CompetitorAnalyzer] TAVILY_API_KEY 없음. {account} 스킵.")
        return []

    client = TavilyClient(api_key=TAVILY_API_KEY)
    results = []

    try:
        data = client.search(
            f"{account} 인스타그램 카드뉴스 최신 콘텐츠 주제",
            search_depth="basic",
            max_results=5,
        )
    except Exception as e:
        print(f"  [CompetitorAnalyzer] Tavily 오류 ({account}): {e}")
        return []

    raw_texts = "\n\n".join(
        f"제목: {r.get('title','')}\n내용: {r.get('content','')[:200]}"
        for r in data.get("results", [])
    )
    if not raw_texts.strip():
        return []

    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.2, api_key=OPENAI_API_KEY)
    structured = llm.with_structured_output(_PostList)
    prompt = ChatPromptTemplate.from_messages([
        ("system", "검색 결과에서 카드뉴스 콘텐츠 정보를 추출하세요. "
                   "각 포스트의 주제, 마케팅 앵글(공감/공포/이익/편의/사회증거 중), "
                   "예상 반응도(낮음/보통/높음), 특이사항을 파악해주세요."),
        ("human", f"계정: {account}\n\n검색 결과:\n{raw_texts}"),
    ])
    try:
        parsed = (prompt | structured).invoke({})
        for p in parsed.posts:
            post = CompetitorPost(
                account=account,
                topic=p.topic,
                angle=p.angle,
                estimated_engagement=p.estimated_engagement,
                url="",
                note=p.note,
            )
            results.append(post)
            insert_competitor(
                account=account,
                topic=p.topic,
                angle=p.angle,
                pattern_note=p.note,
            )
    except Exception as e:
        print(f"  [CompetitorAnalyzer] 파싱 오류 ({account}): {e}")

    return results


# ── 종합 분석 ─────────────────────────────────────────────

def analyze_competitors(accounts: list[str] | None = None) -> CompetitorReport:
    targets = accounts or DEFAULT_COMPETITORS
    all_posts: list[CompetitorPost] = []

    print(f"\n  [CompetitorAnalyzer] {len(targets)}개 계정 분석 시작...")
    for account in targets:
        print(f"    → {account} 크롤링 중...")
        posts = crawl_competitor(account)
        all_posts.extend(posts)
        print(f"       {len(posts)}개 포스트 수집")

    if not all_posts:
        return CompetitorReport(recommendations="수집된 데이터가 없습니다.")

    summary = "\n".join(
        f"[{p.account}] 주제:{p.topic} / 앵글:{p.angle} / 반응:{p.estimated_engagement} / {p.note}"
        for p in all_posts[:30]
    )

    llm = ChatOpenAI(model="gpt-4o", temperature=0.3, api_key=OPENAI_API_KEY)
    structured = llm.with_structured_output(_StrategyReport)
    prompt = ChatPromptTemplate.from_messages([
        ("system", "당신은 SNS 콘텐츠 전략 전문가입니다. "
                   "경쟁 계정 분석 데이터를 바탕으로 '알고'(@algo.kr) 계정의 차별화 전략을 제안하세요.\n"
                   "- top_topics: 경쟁사가 자주 다루는 주제 (우리도 해야 할 것)\n"
                   "- top_angles: 경쟁사가 자주 쓰는 앵글\n"
                   "- gap_opportunities: 경쟁사가 다루지 않는 우리의 차별화 영역\n"
                   "- recommendations: 전략 제안 (한국어, 3~5문장)"),
        ("human", f"경쟁 계정 데이터:\n{summary}"),
    ])
    try:
        report = (prompt | structured).invoke({})
        return CompetitorReport(
            top_topics=report.top_topics,
            top_angles=report.top_angles,
            gap_opportunities=report.gap_opportunities,
            recommendations=report.recommendations,
        )
    except Exception as e:
        print(f"  [CompetitorAnalyzer] 전략 분석 오류: {e}")
        return CompetitorReport(recommendations="분석 중 오류 발생.")


def get_trending_topics_from_competitors(limit: int = 10) -> list[str]:
    """DB에 저장된 경쟁사 데이터에서 자주 등장하는 주제 반환."""
    rows = get_competitors(limit=100)
    from collections import Counter
    topics = [r["topic"] for r in rows if r["topic"]]
    counts = Counter(topics)
    return [t for t, _ in counts.most_common(limit)]


if __name__ == "__main__":
    report = analyze_competitors()
    print(f"\n자주 다루는 주제: {report.top_topics}")
    print(f"자주 쓰는 앵글: {report.top_angles}")
    print(f"차별화 기회: {report.gap_opportunities}")
    print(f"\n전략 제안:\n{report.recommendations}")
