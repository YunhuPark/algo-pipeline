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
from src.utils.rss_content import extract_feed_entry_content, looks_like_article_url

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
    # 1순위 기사에 영상 근거가 부실하면 대신 쓸 2순위 후보. 0이면 대안 없음.
    # 1순위와 마찬가지로 팩트 조건을 만족하는 것 중에서 고르게 한다.
    alt_selected_index: int = 0
    alt_topic: str = ""
    alt_reason: str = ""
    alt_context: str = ""


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
    # "이 계정은 AI·IT·비즈니스 전문"이라 후보 대부분이 "IT"를 포함한다 —
    # 다른 기사와 구분해주는 고유명사가 아니라 카테고리 자체를 가리키는
    # 일반 단어이므로 "ai"처럼 고유명사 앵커에서 제외한다.
    "it",
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
    r"[$₩€£]\s*\d[\d,]*(?:\.\d+)?(?:\s*(?:billion|million|trillion|k))?"  # "$2,200"처럼
    r"|"                                                                   # 통화 기호가 접두된 금액
    r"\d[\d,]*(?:\.\d+)?\s*"
    r"(?:%|퍼센트|퍼센트포인트|조|억|만|천|명|개|건|배|년|개월|주|일|시간|"
    r"달러|원|엔|유로|"
    r"billion|million|trillion|percent|dollars?|users?|people)",
    re.IGNORECASE,
)

_MIN_FACTS = 2            # 이 정도는 있어야 카드 4장에 쓸 수치가 나온다
_MIN_GROUNDED_POOL = 5    # 후보를 좁혀도 선택지가 남을 만큼은 있어야 한다
_MIN_ON_PERSONA_POOL = 2  # AI·IT·비즈니스 후보는 이 정도만 있어도 우선한다
_MIN_AI_POOL = 2          # AI 특화 후보는 이 정도만 있어도 IT·비즈니스보다 우선한다

# 프롬프트로 "AI·IT·비즈니스를 먼저 고르라"고만 해서는, 팩트가 압도적으로
# 많은(예: 27개) 무관한 기사가 있으면 GPT가 그쪽으로 넘어가는 걸 계속
# 봤다 — 카테고리 필터는 프롬프트가 아니라 코드에서 걸어야 안정적이다.
# RSS_FEEDS는 전부 IT 전문 매체라 항상 통과시키고, Tavily(종합 검색) 같은
# 소스만 이 키워드로 걸러 AI·IT·비즈니스 관련 여부를 판단한다.
# \b(단어 경계)는 파이썬 정규식에서 한글도 \w로 취급해, "AI기술"·"IT업계"처럼
# 영문 약어 뒤에 공백 없이 한글이 바로 붙으면 경계가 안 생겨 매칭에 실패한다
# (흔한 한국어 표기인데 못 잡음 → 카테고리 필터가 온퍼소나 후보를 놓침).
# 한글 앞뒤는 신경 안 쓰고 "다른 영문자와 안 붙어있으면 된다"로 좁혀서
# "MAIL"/"WAIT" 같은 단어 내부의 우연한 일치만 막는다.
# AI 자체를 다루는 기사(모델·연구·AI 기업)는 IT/비즈니스 일반 뉴스보다
# 우선한다 — 이 계정의 1순위는 AI이고 IT·비즈니스는 AI 후보가 부족할 때만
# 채우는 보조 카테고리다. _ON_PERSONA_RE(전체 카테고리 게이트)와 별도로
# 유지해, "삼성전자 반도체 실적"처럼 IT/비즈니스이긴 해도 AI 자체를
# 다루지는 않는 기사와 구분한다.
_AI_SPECIFIC_RE = re.compile(
    r"(?<![A-Za-z])AI(?![A-Za-z])|인공지능|생성형|챗봇|LLM|머신러닝|딥러닝|"
    r"신경망|GPT|Claude|클로드|Gemini|제미나이|Llama|라마|Grok|그록|"
    r"오픈AI|openai|오픈에이아이|앤스로픽|anthropic|딥마인드|deepmind|"
    r"엔비디아|nvidia",
    re.IGNORECASE,
)

_ON_PERSONA_RE = re.compile(
    _AI_SPECIFIC_RE.pattern + "|"
    r"(?<![A-Za-z])IT(?![A-Za-z])|아이티|테크|tech|스타트업|startup|"
    r"애플|삼성전자|삼성|구글|google|apple|마이크로소프트|microsoft|"
    r"메타(?!버스)|meta|"
    r"아마존|amazon|테슬라|tesla|소프트웨어|software|하드웨어|hardware|"
    r"반도체|semiconductor|클라우드|cloud|스마트폰|smartphone|로봇|robot|"
    r"블록체인|blockchain|핀테크|fintech|이커머스|커머스|플랫폼|platform|"
    r"디지털|digital|사이버|cyber|알고리즘|algorithm|비트코인|암호화폐|"
    r"가상자산|crypto|주가|증시|코스피|나스닥|상장|유니콘|매출|영업이익|"
    r"시가총액|투자유치|앱\b|(?<![A-Za-z])app(?![A-Za-z])",
    re.IGNORECASE,
)

_RSS_SOURCE_NAMES = {name for name, _ in RSS_FEEDS}

# "2025년 IT 기술 동향 분석 및 핵심 트렌드 예측" 같은 총정리·동향분석형
# 기사는 특정 사건 하나를 뒷받침하기엔 근거가 옅다. 프롬프트로 "이런 기사를
# 고르지 말라"고만 해서는 GPT가 본문 수치가 많다는 이유로 계속 이쪽을 골라,
# topic은 그 안에 스쳐 지나가듯 언급된 세부 사건("애플카 프로젝트 타이탄")
# 으로 짓고 고정 원문은 이 총정리 기사로 남는 불일치가 실제로 반복됐다.
# 코드 단계에서 후보 풀 자체에 안 보이게 거른다.
_ROUNDUP_TITLE_RE = re.compile(
    r"총정리|총망라|한눈에\s*보는|핵심\s*트렌드|트렌드\s*예측|동향\s*분석|"
    r"연간\s*리뷰|결산|올해의|이슈\s*모음|round[- ]?up|year in review|"
    r"trends?\s*(?:to watch|for\s*\d{4})",
    re.IGNORECASE,
)


def _is_roundup_title(title: str) -> bool:
    return bool(_ROUNDUP_TITLE_RE.search(title or ""))


# "그가 이렇게 말했다/주장했다/경고했다"류 발언·의견 인용 기사는 팩트 수치
# 기준(_MIN_FACTS)을 겨우 넘겨도 실제로는 검증 가능한 사건·수치보다 한 사람의
# 주관적 평가가 카드 대부분을 채운다 — "AI 공포는 과장이다"처럼 반응은
# 끌지만 본문이 얄팍해진다. 중립적 사실 보도에도 흔한 "밝혔다"는 일부러
# 빼고, 논쟁·평가성이 뚜렷한 동사·명사만 잡는다.
_OPINION_STATEMENT_RE = re.compile(
    r"주장했다|주장하|경고했다|경고하|비판했다|비판하|반박했다|반박하|"
    r"우려했다|우려하|지적했다|지적하|비난했다|비난하|발언|논란|사설|칼럼|"
    r"오피니언|says|warns?|claims?|slams?|blasts?|accuses?|rejects?",
    re.IGNORECASE,
)


def _is_opinion_statement_title(title: str) -> bool:
    return bool(_OPINION_STATEMENT_RE.search(title or ""))


def _is_ai_specific_candidate(item: NewsItem) -> bool:
    """AI 자체(모델·연구·AI 기업)를 다루는지 — IT/비즈니스 일반과 구분해
    이 계정의 1순위인 AI 후보를 먼저 우선한다."""
    return bool(_AI_SPECIFIC_RE.search(f"{item.title} {item.summary[:200]}"))


def _is_on_persona_candidate(item: NewsItem) -> bool:
    """RSS_FEEDS 출신은 소스 자체가 IT 전문 매체라 항상 통과. 그 외(Tavily
    같은 종합 검색 소스)는 제목·본문 앞부분에 AI·IT·비즈니스 키워드가 있을
    때만 "카테고리 부합" 후보로 인정한다."""
    if item.source in _RSS_SOURCE_NAMES:
        return True
    return bool(_ON_PERSONA_RE.search(f"{item.title} {item.summary[:200]}"))


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

이 계정은 원래 AI·IT·비즈니스 전문 뉴스 계정입니다("사회" 이슈는 AI·IT·
비즈니스와 뚜렷이 맞닿아 있을 때만 예외로 허용하는 부차 카테고리이지,
동등한 한 축이 아닙니다). 그러니 카테고리부터 걸러낸 다음에 그 안에서
품질(수치·근거)로 고르십시오 — 수치가 아무리 많아도 카테고리가 안 맞으면
탈락입니다.

선택 기준 (위에서부터 우선):
1. AI·IT·비즈니스 기사인가? 후보 중 하나라도 있으면 반드시 그 안에서만
   고르십시오. 대중교통·행정·생활 밀착 "사회" 뉴스(예: 버스 노선, 공휴일
   지정, 지역 축제)는 후보 전체에 AI·IT·비즈니스 기사가 단 하나도 없을
   때만 마지막 수단으로 고르십시오. 이 안에서도 AI(모델·연구·AI 기업)를
   직접 다루는 기사가 있으면 IT/비즈니스 일반 기사보다 우선하십시오.
2. 연예인 스캔들, 강력범죄, 여야 정쟁성 공방, 자극적인 사건·사고 단신은
   카테고리·수치와 무관하게 절대 고르지 마십시오
3. 본문에 구체적 수치(금액·비율·건수·날짜)와 그 수치의 주체가 실제로 적혀 있는 기사
4. 누가 무엇을 했는지가 명확한 기사. "일부 전문가들", "논란이 제기된다" 수준의
   익명·수동 서술만 있는 논평·사설·오피니언은 피하십시오
   (제목이 "~인가?", "Is ...?"처럼 질문형이고 본문에 수치가 없으면 대개 논평입니다).
   특정 인물이 실명으로 한 발언·주장·경고를 다루는 기사도, 그 발언 자체가
   내용의 전부이고 독립적으로 검증 가능한 사건·수치가 따로 없다면 피하십시오
   — "OOO가 이렇게 말했다"는 카드로 만들어도 근거 없는 의견처럼 읽힙니다.
5. 서로 다른 핵심 사실을 4개 이상 뽑을 수 있을 만큼 본문이 충분한 기사
6. 위 조건을 만족하는 것 중에서 MZ세대가 "와 이거 알아야 해!" 라고 느낄 주제
7. 지나치게 특정 정치적 편향이 없는 것
8. 선택한 한 기사의 고유명사·제품명·핵심 수치를 topic에 그대로 유지할 것
8-1. topic은 선택한 기사의 제목이 다루는 주된 사건이어야 합니다. "2025년 IT
     동향 총정리", "올해의 트렌드 총정리"처럼 여러 소식을 나열하는 동향·총정리형
     기사를 골랐다면, 그 안에서 잠깐 스쳐가듯 언급된 세부 사례 하나만 뽑아
     topic으로 삼지 마십시오 — 그런 세부 사례는 그 기사의 본문만으로는 독립적인
     근거가 부족합니다. 총정리 기사 안의 한 사례가 마음에 든다면, 그 기사를
     선택하는 대신 그 사례를 다룬 다른 후보(있다면)를 선택하십시오.
9. "AI 필수 용어", "알아야 할 것", "최신 트렌드" 같은 포괄적 주제로 바꾸지 말 것

selected_index: 선택한 헤드라인의 번호 (1부터 시작)
topic: 카드뉴스 제목으로 쓸 간결한 주제명 (예: "애플 AI 전략 대전환")
reason: 왜 이 주제를 선택했는지 한 줄
context: 카드뉴스 작성에 필요한 핵심 배경 정보 3~5문장

또한 1순위와 별개로, 위 조건을 비슷한 수준으로 만족하는 서로 다른 기사를
2순위 대안으로 하나 더 골라주세요 (alt_*). 실제 생성 단계에서 1순위 기사에
쓸 만한 영상 자료가 전혀 없을 때만 대신 씁니다. 마땅한 대안이 없으면
alt_selected_index를 0으로 두세요.
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
    # 해외 RSS(TechCrunch 등)는 본문 대신 한두 문장짜리 부제만 오는 경우가
    # 많아, 정작 핵심 수치는 제목에만 있고 summary는 0건으로 잡히는 일이
    # 잦았다(예: "$18 million"가 제목에만 있음) — 제목도 함께 스캔한다.
    fact_counts = {
        idx: _fact_count(f"{it.title} {it.summary}") for idx, it in candidates
    }

    # 후보 압축 4단계 — 우선순위: (1) AI 특화 + 팩트 충분 → (2) AI·IT·비즈니스
    # 전체 + 팩트 충분 → (3) 팩트만 충분(카테고리 무관) → (4) 전체. 프롬프트
    # 지시만으로는 사회 뉴스가 팩트를 아주 많이 담고 있을 때 그쪽으로 넘어가는
    # 걸 막지 못해, 코드 단계에서 먼저 카테고리로 걸러 GPT에게 애초에 후보로
    # 보여주지 않는다.
    grounded = [(idx, it) for idx, it in candidates if fact_counts[idx] >= _MIN_FACTS]
    ai_grounded = [
        (idx, it) for idx, it in grounded if _is_ai_specific_candidate(it)
    ]
    on_persona_grounded = [
        (idx, it) for idx, it in grounded if _is_on_persona_candidate(it)
    ]
    if len(ai_grounded) >= _MIN_AI_POOL:
        pool = ai_grounded
        print(
            f"  [NewsCollector] AI 특화 + 본문 수치 {_MIN_FACTS}개 이상 "
            f"기사 {len(pool)}건으로 후보 압축 (AI 최우선)"
        )
    elif len(on_persona_grounded) >= _MIN_ON_PERSONA_POOL:
        pool = on_persona_grounded
        print(
            f"  [NewsCollector] AI 후보 부족({len(ai_grounded)}건) → AI·IT·비즈니스 + "
            f"본문 수치 {_MIN_FACTS}개 이상 기사 {len(pool)}건으로 후보 압축"
        )
    elif len(grounded) >= _MIN_GROUNDED_POOL:
        pool = grounded
        print(
            f"  [NewsCollector] 카테고리 부합 후보 부족({len(on_persona_grounded)}건) "
            f"→ 본문 수치 {_MIN_FACTS}개 이상 기사 {len(grounded)}건에서 선택"
        )
    else:
        pool = candidates
        print(
            f"  [NewsCollector] 수치 있는 기사 {len(grounded)}건뿐 → 전체 "
            f"{len(candidates)}건에서 선택"
        )

    non_roundup_pool = [(idx, it) for idx, it in pool if not _is_roundup_title(it.title)]
    if len(non_roundup_pool) >= _MIN_ON_PERSONA_POOL and len(non_roundup_pool) < len(pool):
        print(
            f"  [NewsCollector] 총정리·동향분석형 기사 {len(pool) - len(non_roundup_pool)}건 "
            f"제외 → {len(non_roundup_pool)}건에서 선택"
        )
        pool = non_roundup_pool

    non_opinion_pool = [
        (idx, it) for idx, it in pool if not _is_opinion_statement_title(it.title)
    ]
    if len(non_opinion_pool) >= _MIN_ON_PERSONA_POOL and len(non_opinion_pool) < len(pool):
        print(
            f"  [NewsCollector] 발언·의견 중심 기사 {len(pool) - len(non_opinion_pool)}건 "
            f"제외 → {len(non_opinion_pool)}건에서 선택"
        )
        pool = non_opinion_pool

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

    # alt도 같은 방식으로 pool 기준 → items 기준으로 되돌린다. 범위를 벗어나면
    # (LLM이 이상한 번호를 주면) 대안 없음으로 취급 — 잘못된 기사로 바뀌는 것보다 안전하다.
    if selected.alt_selected_index and 1 <= selected.alt_selected_index <= len(pool):
        alt_pool_pos = selected.alt_selected_index - 1
        alt_original_idx, _ = pool[alt_pool_pos]
        selected.alt_selected_index = alt_original_idx + 1
    else:
        selected.alt_selected_index = 0

    return selected


# ── 영상 커버리지 사전 확인 ────────────────────────────────

def _quick_video_coverage(article_title: str, topic: str) -> int:
    """주제 선택 단계에서 쓸 가벼운 영상 후보 개수 추정.

    Phase 1.5의 정식 탐색(youtube_fetcher.fetch_video_candidates, 키워드
    6개·조회수 2000+)보다 키워드/후보 수를 줄여 빠르게 대략적인 커버리지만
    잰다. 실패해도(네트워크, rate limit 등) 주제 선택 자체를 막지 않도록
    -1(확인 불가)을 돌려주고 호출부는 이를 "판단 보류"로 취급한다.
    """
    try:
        from src.agents.youtube_fetcher import fetch_video_candidates

        candidates = fetch_video_candidates(
            article_title=article_title,
            topic=topic,
            n_keywords=3,
            n_per=3,
            days=60,
            min_views=1000,
        )
        return len(candidates)
    except Exception as e:
        print(f"  [NewsCollector] 영상 커버리지 확인 실패({type(e).__name__}) — 무시하고 계속")
        return -1


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
        if item.title.strip()
        and item.url.startswith(("http://", "https://"))
        and looks_like_article_url(item.url)
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

    # 1순위 기사에 쓸 영상이 거의 없어 보이면, GPT가 함께 제안한 2순위
    # 대안의 영상 커버리지를 확인해 더 나은 쪽으로 바꾼다. 확인 자체가
    # 실패하면(-1) 원래 선택을 그대로 둔다 — 이 단계는 어디까지나 보조 신호다.
    if selected.alt_selected_index:
        primary_coverage = _quick_video_coverage(selected_item.title, source_locked_topic)
        print(
            "  [NewsCollector] 1순위 영상 커버리지: "
            f"{primary_coverage if primary_coverage >= 0 else '확인불가'}"
        )
        if 0 <= primary_coverage < 2:
            alt_index = max(1, min(selected.alt_selected_index, len(all_items)))
            alt_item = all_items[alt_index - 1]
            alt_topic = _ensure_source_locked_topic(
                selected.alt_topic or selected.topic, alt_item.title
            )
            alt_coverage = _quick_video_coverage(alt_item.title, alt_topic)
            print(
                f"  [NewsCollector] 2순위 '{alt_topic}' 영상 커버리지: "
                f"{alt_coverage if alt_coverage >= 0 else '확인불가'}"
            )
            if alt_coverage > primary_coverage:
                print(f"  [NewsCollector] 영상 커버리지 기준으로 2순위 주제 채택")
                selected_item = alt_item
                source_locked_topic = alt_topic
                selected.topic = selected.alt_topic or selected.topic
                selected.reason = selected.alt_reason or selected.reason
                selected.context = selected.alt_context or selected.context

    # ── 주제-원문 앵커 사전 확인 ──────────────────────────────
    # LLM이 총정리성 기사 안의 세부 사례 하나만 뽑아 topic으로 삼으면, 정작
    # 고정 원문(selected_item)에는 그 topic의 핵심 앵커가 없어 다운스트림
    # (assert_source_lineage_matches_topic)에서 값비싼 원문 추출을 이미 마친
    # 뒤에야 실패한다. 같은 검증을 여기서 먼저 돌려, 실패 시 GPT가 함께 고른
    # 2순위 대안이 앵커와 맞는지 확인하고 맞으면 그쪽으로 바꾼다.
    from src.qa.topic_source_guard import topic_matches_source

    if not topic_matches_source(source_locked_topic, selected_item.title, selected_item.summary):
        print(
            f"  ⚠️ [NewsCollector] 선택 주제 '{source_locked_topic}'가 원문 "
            f"'{selected_item.title}'의 핵심 앵커와 겹치지 않습니다."
        )
        if selected.alt_selected_index and 1 <= selected.alt_selected_index <= len(all_items):
            alt_candidate_item = all_items[selected.alt_selected_index - 1]
            if alt_candidate_item.url != selected_item.url:
                alt_candidate_topic = _ensure_source_locked_topic(
                    selected.alt_topic or selected.topic, alt_candidate_item.title
                )
                if topic_matches_source(
                    alt_candidate_topic, alt_candidate_item.title, alt_candidate_item.summary
                ):
                    print(f"  → 앵커가 맞는 2순위 주제로 교체: '{alt_candidate_topic}'")
                    selected_item = alt_candidate_item
                    source_locked_topic = alt_candidate_topic
                    selected.reason = selected.alt_reason or selected.reason
                    selected.context = selected.alt_context or selected.context
        if not topic_matches_source(source_locked_topic, selected_item.title, selected_item.summary):
            print("  ⚠️ 대안도 앵커가 맞지 않음 — 다운스트림 검증에서 최종 판단합니다.")

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
