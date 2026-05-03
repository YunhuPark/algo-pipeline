"""
NewsCollector — 최신 뉴스 자동 수집 + GPT-4o 주제 선택
────────────────────────────────────────────────────────
1. 다중 RSS 피드에서 최신 헤드라인 수집
2. Tavily Search로 실시간 트렌드 보완
3. GPT-4o가 카드뉴스로 만들기 가장 좋은 주제 선택 + 이유 설명
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import feedparser
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel
from tavily import TavilyClient

from src.config import OPENAI_API_KEY, TAVILY_API_KEY, LLM_MODEL

# ── RSS 피드 목록 (한국 + 글로벌 주요 뉴스) ───────────────
RSS_FEEDS = [
    # 한국 뉴스
    ("연합뉴스 IT",       "https://www.yna.co.kr/rss/it.xml"),
    ("ZDNet Korea",       "https://zdnet.co.kr/rss/"),
    ("전자신문",           "https://www.etnews.com/rss/allArticleList.xml"),
    ("IT조선",            "https://it.chosun.com/rss/rss.html"),
    ("동아IT",            "https://it.donga.com/rss/index.xml"),
    # 글로벌 뉴스
    ("TechCrunch",        "https://techcrunch.com/feed/"),
    ("The Verge",         "https://www.theverge.com/rss/index.xml"),
    ("Ars Technica",      "https://feeds.arstechnica.com/arstechnica/index"),
    ("MIT Tech Review",   "https://www.technologyreview.com/feed/"),
    ("VentureBeat AI",    "https://venturebeat.com/category/ai/feed/"),
]

# 24시간 이내 뉴스만 수집
HOURS_LIMIT = 24


@dataclass
class NewsItem:
    title: str
    summary: str
    source: str
    url: str
    published: Optional[datetime] = None


@dataclass
class NewsSelection:
    topic: str          # GPT-4o가 선택한 최종 주제 (카드뉴스 제목용)
    reason: str         # 선택 이유
    context: str        # 배경 정보 (content_creator에 주입)
    source_items: list[NewsItem] = field(default_factory=list)


# ── Pydantic 스키마 (structured output) ──────────────────

class _SelectedTopic(BaseModel):
    topic: str
    reason: str
    context: str


# ── RSS 수집 ──────────────────────────────────────────────

def _parse_rss_feeds(limit_hours: int = HOURS_LIMIT) -> list[NewsItem]:
    cutoff = datetime.now() - timedelta(hours=limit_hours)
    items: list[NewsItem] = []

    for source_name, url in RSS_FEEDS:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:10]:  # 피드당 최대 10개
                # 날짜 파싱
                pub = None
                if hasattr(entry, "published_parsed") and entry.published_parsed:
                    try:
                        pub = datetime(*entry.published_parsed[:6])
                    except Exception:
                        pass

                # 너무 오래된 기사 스킵
                if pub and pub < cutoff:
                    continue

                title   = getattr(entry, "title",   "").strip()
                summary = getattr(entry, "summary",  "").strip()[:300]
                link    = getattr(entry, "link",     "")

                if not title:
                    continue

                items.append(NewsItem(
                    title=title,
                    summary=summary,
                    source=source_name,
                    url=link,
                    published=pub,
                ))
        except Exception as e:
            print(f"  [NewsCollector] RSS 실패 ({source_name}): {e}")

    print(f"  [NewsCollector] RSS 수집: {len(items)}개 기사")
    return items


def _fetch_tavily_trends(query: str = "오늘 주요 뉴스 AI IT 트렌드") -> list[NewsItem]:
    if not TAVILY_API_KEY:
        return []
    try:
        client = TavilyClient(api_key=TAVILY_API_KEY)
        results = client.search(query, search_depth="basic", max_results=10)
        items = []
        for r in results.get("results", []):
            items.append(NewsItem(
                title=r.get("title", ""),
                summary=r.get("content", "")[:300],
                source="Tavily",
                url=r.get("url", ""),
            ))
        print(f"  [NewsCollector] Tavily 수집: {len(items)}개")
        return items
    except Exception as e:
        print(f"  [NewsCollector] Tavily 실패: {e}")
        return []


# ── GPT-4o 주제 선택 ─────────────────────────────────────

_SYSTEM = """
당신은 인스타그램 카드뉴스 계정 '알고'의 편집장입니다.
매일 수집된 뉴스 헤드라인 중에서 카드뉴스로 만들기 가장 좋은 주제 하나를 선택합니다.

선택 기준:
- MZ세대가 "와 이거 알아야 해!" 라고 느낄 주제
- 설명할 내용이 충분히 있어서 6장 카드뉴스를 채울 수 있는 주제
- AI, IT, 비즈니스, 사회 이슈 중 파급력이 큰 것
- 지나치게 특정 정치적 편향이 없는 것

topic: 카드뉴스 제목으로 쓸 간결한 주제명 (예: "애플 AI 전략 대전환")
reason: 왜 이 주제를 선택했는지 한 줄
context: 카드뉴스 작성에 필요한 핵심 배경 정보 3~5문장
"""

_HUMAN = """
오늘 수집된 뉴스 헤드라인 목록입니다 (현재 날짜: {today}):

{headlines}

위 헤드라인 중에서 오늘 카드뉴스로 만들 주제 하나를 선택해주세요.
반드시 {today} 기준의 최신 내용만 다루세요. 과거 연도(2025년 등)를 제목에 쓰지 마세요.
"""


def _select_topic_with_gpt(items: list[NewsItem]) -> _SelectedTopic:
    headlines = "\n".join(
        f"[{i+1}] ({it.source}) {it.title}"
        for i, it in enumerate(items[:40])  # 최대 40개 헤드라인
    )

    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.3, api_key=OPENAI_API_KEY)
    structured_llm = llm.with_structured_output(_SelectedTopic)
    prompt = ChatPromptTemplate.from_messages([
        ("system", _SYSTEM),
        ("human",  _HUMAN),
    ])
    chain = prompt | structured_llm
    today = datetime.now().strftime("%Y년 %m월 %d일")
    return chain.invoke({"headlines": headlines, "today": today})


# ── 공개 API ──────────────────────────────────────────────

def collect_and_select() -> NewsSelection:
    """
    뉴스 수집 → GPT-4o 주제 선택 → NewsSelection 반환.
    pipeline.py에서 topic 대신 이 결과를 주입.
    """
    print("\n[NewsCollector] 뉴스 수집 시작...")

    rss_items = _parse_rss_feeds()
    tavily_items = _fetch_tavily_trends()
    all_items = rss_items + tavily_items

    if not all_items:
        raise RuntimeError("수집된 뉴스가 없습니다. 네트워크 연결을 확인하세요.")

    print(f"  [NewsCollector] 총 {len(all_items)}개 기사 → GPT-4o 주제 선택 중...")
    selected = _select_topic_with_gpt(all_items)

    print(f"  [NewsCollector] 선택된 주제: {selected.topic}")
    print(f"  [NewsCollector] 이유: {selected.reason}")

    return NewsSelection(
        topic=selected.topic,
        reason=selected.reason,
        context=selected.context,
        source_items=all_items[:10],
    )


if __name__ == "__main__":
    result = collect_and_select()
    print(f"\n최종 주제: {result.topic}")
    print(f"이유: {result.reason}")
    print(f"배경: {result.context}")
