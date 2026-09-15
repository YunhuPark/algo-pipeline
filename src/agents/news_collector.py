"""
NewsCollector — 최신 뉴스 자동 수집 + GPT-4o 주제 선택
────────────────────────────────────────────────────────
1. 다중 RSS 피드에서 최신 헤드라인 수집
2. Tavily Search로 실시간 트렌드 보완
3. GPT-4o가 카드뉴스로 만들기 가장 좋은 주제 선택 + 이유 설명
"""
from __future__ import annotations

import re
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
from src.utils.rss_content import extract_feed_entry_content

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
    selected_item: NewsItem | None = None


# ── Pydantic 스키마 (structured output) ──────────────────

class _SelectedTopic(BaseModel):
    selected_index: int
    topic: str
    reason: str
    context: str


_SOURCE_ANCHOR_STOPWORDS = {
    "about",
    "after",
    "ai",
    "amid",
    "and",
    "are",
    "but",
    "for",
    "from",
    "how",
    "into",
    "new",
    "says",
    "the",
    "this",
    "what",
    "when",
    "why",
    "with",
}


def _source_anchor(source_title: str) -> str:
    """Return the first likely proper-name anchor from an English headline."""

    for token in re.findall(r"[A-Za-z][A-Za-z0-9.+-]*", source_title or ""):
        if len(token) < 2 or token.lower() in _SOURCE_ANCHOR_STOPWORDS:
            continue
        if token[0].isupper() or any(char.isupper() or char.isdigit() for char in token[1:]):
            return token
    return ""


def _ensure_source_locked_topic(topic: str, source_title: str) -> str:
    """Keep the selected article's proper name in an otherwise generic topic."""

    clean_topic = (topic or "").strip()
    anchor = _source_anchor(source_title)
    if not anchor:
        return clean_topic
    pattern = rf"(?<![A-Za-z0-9]){re.escape(anchor)}(?![A-Za-z0-9])"
    if re.search(pattern, clean_topic, flags=re.IGNORECASE):
        return clean_topic
    return f"{anchor} {clean_topic}".strip()


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
                # content:encoded가 있으면 전문을 보존한다. 300자 preview만
                # 저장하면 Tavily extract가 실패하는 순간 근거가 사라진다.
                summary = extract_feed_entry_content(entry)
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

# 카드가 실을 수 있는 건 근거로 확인되는 사실뿐이다. 본문에 단위가 붙은 숫자가
# 하나도 없는 기사(주로 논평·사설)를 고르면 "~라는 의견이 있습니다" 수준의
# 카드밖에 나오지 않으므로, 선택 단계에서 본문의 구체성을 신호로 쓴다.
_FACT_TOKEN_RE = re.compile(
    r"\d[\d,]*(?:\.\d+)?\s*"
    r"(?:%|퍼센트|퍼센트포인트|조|억|만|천|명|개|건|배|년|개월|주|일|시간|"
    r"달러|원|엔|유로|"
    r"billion|million|trillion|percent|dollars?|users?|people)",
    re.IGNORECASE,
)

_MIN_FACTS = 2            # 이 정도는 있어야 카드 4장에 쓸 수치가 나온다
_MIN_GROUNDED_POOL = 5    # 후보를 좁혀도 선택지가 남을 만큼은 있어야 한다


def _fact_count(text: str) -> int:
    """본문에 등장하는 '수치+단위' 표현의 가짓수."""
    return len({
        m.group(0).strip().casefold()
        for m in _FACT_TOKEN_RE.finditer(text or "")
    })


def _excerpt(text: str, limit: int = 120) -> str:
    clean = re.sub(r"\s+", " ", text or "").strip()
    if not clean:
        return "(본문 없음)"
    return clean if len(clean) <= limit else clean[:limit] + "…"

_SYSTEM = """
당신은 인스타그램 카드뉴스 계정 '알고'의 편집장입니다.
매일 수집된 뉴스 중에서 카드뉴스로 만들기 가장 좋은 주제 하나를 선택합니다.

카드는 "근거로 확인된 내용"을 싣습니다. 따라서 제목이 아무리 흥미로워도
본문에 확인 가능한 사실이 없으면 "~라는 의견이 있습니다" 수준의 카드밖에
나오지 않습니다. 본문에 무엇이 들어있는지를 최우선으로 보고 고르십시오.

선택 기준 (위에서부터 우선):
- 본문에 구체적 수치(금액·비율·건수·날짜)와 그 수치의 주체가 실제로 적혀 있는 기사
- 누가 무엇을 했는지가 명확한 기사. "일부 전문가들", "논란이 제기된다" 수준의
  익명·수동 서술만 있는 논평·사설·오피니언은 피하십시오
  (제목이 "~인가?", "Is ...?"처럼 질문형이고 본문에 수치가 없으면 대개 논평입니다)
- 서로 다른 핵심 사실을 4개 이상 뽑을 수 있을 만큼 본문이 충분한 기사
- 위 조건을 만족하는 것 중에서 MZ세대가 "와 이거 알아야 해!" 라고 느낄 주제
- AI, IT, 비즈니스, 사회 이슈 중 파급력이 큰 것
- 지나치게 특정 정치적 편향이 없는 것
- 선택한 한 기사의 고유명사·제품명·핵심 수치를 topic에 그대로 유지할 것
- "AI 필수 용어", "알아야 할 것", "최신 트렌드" 같은 포괄적 주제로 바꾸지 말 것

selected_index: 선택한 헤드라인의 번호 (1부터 시작)
topic: 카드뉴스 제목으로 쓸 간결한 주제명 (예: "애플 AI 전략 대전환")
reason: 왜 이 주제를 선택했는지 한 줄
context: 카드뉴스 작성에 필요한 핵심 배경 정보 3~5문장
"""

_HUMAN = """
오늘 수집된 뉴스 목록입니다 (현재 날짜: {today}).
각 항목의 '수치'는 본문에서 단위가 붙은 숫자 표현이 몇 가지 발견됐는지이며,
그 아래는 본문 앞부분입니다.

{headlines}

위 목록에서 오늘 카드뉴스로 만들 주제 하나를 선택해주세요.
본문이 비어 있거나 수치가 0개인 항목은 마지막 수단으로만 고르십시오.
반드시 {today} 기준의 최신 내용만 다루세요. 과거 연도(2025년 등)를 제목에 쓰지 마세요.
"""


def _select_topic_with_gpt(items: list[NewsItem]) -> _SelectedTopic:
    """주제를 고르고, selected_index를 items 기준 1-based로 맞춰 돌려준다."""
    candidates = list(enumerate(items[:40]))   # (원본 인덱스, 기사)
    fact_counts = {idx: _fact_count(it.summary) for idx, it in candidates}

    # 수치가 있는 기사가 충분히 모이면 그쪽만 후보로 좁힌다. 부족하면 전체를
    # 그대로 두어, 조건을 못 맞춘다고 생성 자체가 막히는 일은 없게 한다.
    grounded = [(idx, it) for idx, it in candidates if fact_counts[idx] >= _MIN_FACTS]
    pool = grounded if len(grounded) >= _MIN_GROUNDED_POOL else candidates
    if pool is grounded:
        print(
            f"  [NewsCollector] 본문 수치 {_MIN_FACTS}개 이상 기사 "
            f"{len(grounded)}건으로 후보 압축"
        )
    else:
        print(
            f"  [NewsCollector] 수치 있는 기사 {len(grounded)}건뿐 → 전체 "
            f"{len(candidates)}건에서 선택"
        )

    headlines = "\n".join(
        f"[{n+1}] ({it.source}) {it.title} — 수치 {fact_counts[idx]}개\n"
        f"    본문: {_excerpt(it.summary)}"
        for n, (idx, it) in enumerate(pool)
    )

    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.3, api_key=OPENAI_API_KEY)
    structured_llm = llm.with_structured_output(_SelectedTopic)
    prompt = ChatPromptTemplate.from_messages([
        ("system", _SYSTEM),
        ("human",  _HUMAN),
    ])
    chain = prompt | structured_llm
    today = datetime.now().strftime("%Y년 %m월 %d일")
    selected = chain.invoke({"headlines": headlines, "today": today})

    # LLM이 준 번호는 pool 기준이다. 호출부는 items 기준으로 쓰므로 여기서 되돌린다.
    pool_pos = max(1, min(selected.selected_index, len(pool))) - 1
    original_idx, chosen = pool[pool_pos]
    selected.selected_index = original_idx + 1
    print(f"  [NewsCollector] 선택 기사 본문 수치: {fact_counts[original_idx]}개")
    return selected


# ── 공개 API ──────────────────────────────────────────────

def collect_and_select() -> NewsSelection:
    """
    뉴스 수집 → GPT-4o 주제 선택 → NewsSelection 반환.
    pipeline.py에서 topic 대신 이 결과를 주입.
    """
    print("\n[NewsCollector] 뉴스 수집 시작...")

    rss_items = _parse_rss_feeds()
    tavily_items = _fetch_tavily_trends()
    all_items = [
        item
        for item in rss_items + tavily_items
        if item.title.strip() and item.url.startswith(("http://", "https://"))
    ]

    if not all_items:
        raise RuntimeError("수집된 뉴스가 없습니다. 네트워크 연결을 확인하세요.")

    print(f"  [NewsCollector] 총 {len(all_items)}개 기사 → GPT-4o 주제 선택 중...")
    selected = _select_topic_with_gpt(all_items)
    selected_index = max(1, min(selected.selected_index, min(len(all_items), 40)))
    selected_item = all_items[selected_index - 1]
    source_locked_topic = _ensure_source_locked_topic(
        selected.topic,
        selected_item.title,
    )

    if source_locked_topic != selected.topic.strip():
        print(
            "  [NewsCollector] 원문 고유명사 복원: "
            f"'{selected.topic}' → '{source_locked_topic}'"
        )
    print(f"  [NewsCollector] 선택된 주제: {source_locked_topic}")
    print(f"  [NewsCollector] 고정 원문: {selected_item.title}")
    print(f"  [NewsCollector] 이유: {selected.reason}")

    return NewsSelection(
        topic=source_locked_topic,
        reason=selected.reason,
        context=selected.context,
        source_items=all_items[:10],
        selected_item=selected_item,
    )


if __name__ == "__main__":
    result = collect_and_select()
    print(f"\n최종 주제: {result.topic}")
    print(f"이유: {result.reason}")
    print(f"배경: {result.context}")
