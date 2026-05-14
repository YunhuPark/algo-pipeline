"""
Phase 1: Trend Analyzer — 멀티소스 실시간 AI 뉴스 수집
─────────────────────────────────────────────────────────
소스 우선순위 (빠른 것 → 중요한 것 순):

  Tier 1 | 공식 AI 랩 블로그  (score 2.0)
           OpenAI News, HuggingFace Blog, Google AI Blog 등
  Tier 2 | 커뮤니티 실시간    (score 1.5 이상)
           Hacker News (무료 API), Reddit AI 서브레딧 (RSS)
  Tier 3 | 영문 IT 미디어     (score 1.0)
           TechCrunch AI, VentureBeat AI, MIT Tech Review, Wired
  Tier 4 | 국내 IT 미디어     (score 0.8)
           AI타임스, ZDNet Korea, 전자신문, IT조선
  Social | Tavily X/Twitter 콘텐츠 보완 검색
  Fallback| Tavily 뉴스 검색  (소스가 부족할 때만)
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

import feedparser
import httpx
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel

from src.config import TAVILY_API_KEY, OPENAI_API_KEY
from src.schemas.card_news import TrendReport, TrendResult

# ── Tier 1: 공식 AI 랩 / 연구기관 블로그 ─────────────────
RSS_TIER1: list[tuple[str, str]] = [
    ("OpenAI News",        "https://openai.com/news/rss.xml"),
    ("HuggingFace Blog",   "https://huggingface.co/blog/feed.xml"),
    ("Google AI Blog",     "https://blog.google/technology/ai/rss/"),
    ("Google DeepMind",    "https://deepmind.google/blog/rss/"),
    ("Meta AI Blog",       "https://ai.meta.com/blog/feed/"),
    ("Anthropic News",     "https://www.anthropic.com/news/rss.xml"),
    ("Mistral AI",         "https://mistral.ai/blog/feed.xml"),
    ("xAI Blog",           "https://x.ai/blog/feed"),
    ("Papers With Code",   "https://paperswithcode.com/latest/rss"),
]

# ── Tier 3: 영문 IT 미디어 ────────────────────────────────
RSS_TIER3_EN: list[tuple[str, str]] = [
    ("TechCrunch AI",      "https://techcrunch.com/category/artificial-intelligence/feed/"),
    ("VentureBeat AI",     "https://venturebeat.com/category/ai/feed/"),
    ("MIT Tech Review AI", "https://www.technologyreview.com/topic/artificial-intelligence/feed"),
    ("Wired AI",           "https://www.wired.com/tag/artificial-intelligence/feed/rss"),
    ("Ars Technica Tech",  "https://feeds.arstechnica.com/arstechnica/technology-lab"),
    ("The Verge",          "https://www.theverge.com/rss/index.xml"),
]

# ── Tier 4: 국내 IT 미디어 ────────────────────────────────
RSS_TIER4_KR: list[tuple[str, str]] = [
    ("AI타임스",    "https://www.aitimes.com/rss/allArticle.xml"),
    ("ZDNet Korea", "https://zdnet.co.kr/rss/"),
    ("전자신문",     "https://www.etnews.com/rss/allArticleList.xml"),
    ("IT조선",      "https://it.chosun.com/rss/rss.html"),
    ("연합뉴스 IT", "https://www.yna.co.kr/rss/it.xml"),
    ("TechCrunch",  "https://techcrunch.com/feed/"),
]

# ── Reddit AI 서브레딧 (RSS) ──────────────────────────────
REDDIT_RSS: list[tuple[str, str]] = [
    ("r/artificial",       "https://www.reddit.com/r/artificial/hot/.rss"),
    ("r/MachineLearning",  "https://www.reddit.com/r/MachineLearning/hot/.rss"),
    ("r/LocalLLaMA",       "https://www.reddit.com/r/LocalLLaMA/hot/.rss"),
    ("r/singularity",      "https://www.reddit.com/r/singularity/hot/.rss"),
    ("r/OpenAI",           "https://www.reddit.com/r/OpenAI/hot/.rss"),
]

# ── 중요도 판단 기준 ──────────────────────────────────────
TIER1_DOMAINS = {
    "openai.com", "anthropic.com", "deepmind.google", "ai.meta.com",
    "mistral.ai", "x.ai", "huggingface.co", "cohere.com", "ai.google",
    "blog.google", "paperswithcode.com",
}

# Tier 2 키워드 (제목에 포함 시 점수 상승)
TIER2_COMPANIES = [
    "OpenAI", "Anthropic", "Google DeepMind", "Meta AI", "Microsoft", "Apple",
    "NVIDIA", "Amazon", "xAI", "Grok", "Claude", "GPT-", "Gemini", "Llama",
    "Mistral", "Perplexity", "Midjourney", "Sora", "Runway", "Stability AI",
]

# 주제별 키워드 (영어+한국어)
TOPIC_KEYWORDS: dict[str, list[str]] = {
    "AI": [
        "AI", "artificial intelligence", "인공지능", "GPT", "LLM", "model", "모델",
        "agent", "에이전트", "deep learning", "딥러닝", "machine learning",
        "생성형", "chatbot", "Claude", "Gemini", "Llama", "diffusion",
    ],
    "IT": ["IT", "tech", "테크", "software", "소프트웨어", "app", "앱", "platform", "startup"],
    "경제": ["economy", "경제", "stock", "주식", "invest", "투자"],
    "사회": ["사회", "정치", "government", "정부", "policy"],
}


class _BestArticle(BaseModel):
    index: int
    reason: str
    related_indices: list[int] = []   # 함께 참고할 보조 기사 번호 목록 (최대 2개)


def _is_duplicate_topic(title: str, recent_topics: list[str], threshold: float = 0.6) -> bool:
    """
    새 기사 제목이 최근 발행 주제와 너무 유사한지 단어 겹침으로 판단.
    threshold: 공통 단어 비율 (0.6 = 60% 이상 겹치면 중복)
    """
    if not recent_topics:
        return False

    def _tokens(text: str) -> set[str]:
        # 영문은 소문자 단어 분리, 한글은 2글자 이상 단어 분리
        words = re.findall(r"[A-Za-z]{3,}|[가-힣]{2,}", text)
        return {w.lower() for w in words}

    title_tokens = _tokens(title)
    if not title_tokens:
        return False

    for recent in recent_topics:
        recent_tokens = _tokens(recent)
        if not recent_tokens:
            continue
        overlap = len(title_tokens & recent_tokens)
        ratio = overlap / min(len(title_tokens), len(recent_tokens))
        if ratio >= threshold:
            return True
    return False


def _select_diverse_candidates(all_results: list[TrendResult], n: int = 12) -> list[TrendResult]:
    """
    점수 순으로만 뽑으면 Tier1 블로그가 독식 → 소스 다양성 보장.
    Tier1(공식블로그) 최대 4개 + 커뮤니티(HN/Reddit) 최대 4개 + 기타 최대 4개.
    """
    # 월간 정리·뉴스레터 류 사전 제거 (기사 자체 내용 없음)
    roundup_patterns = ["the latest", "monthly", "weekly", "roundup", "newsletter",
                        "이달의", "이번 주", "주간", "월간", "뉴스레터"]
    def _is_roundup(r: TrendResult) -> bool:
        t = r.title.lower()
        return any(p in t for p in roundup_patterns)

    filtered = [r for r in all_results if not _is_roundup(r)]
    tier1  = [r for r in filtered if r.score >= 2.0][:5]
    tier2  = [r for r in filtered if 1.4 <= r.score < 2.0][:4]
    tier3  = [r for r in filtered if r.score < 1.4][:4]
    seen: set[str] = set()
    result: list[TrendResult] = []
    for r in tier1 + tier2 + tier3:
        if r.url not in seen:
            seen.add(r.url)
            result.append(r)
    return result[:n]


# ── 유틸리티 ──────────────────────────────────────────────

def _get_keywords(topic: str) -> list[str]:
    for key, kws in TOPIC_KEYWORDS.items():
        if key.lower() in topic.lower() or any(k.lower() in topic.lower() for k in kws):
            return kws
    return topic.split()


def _calc_score(title: str, url: str, pub: datetime | None, base: float = 1.0) -> float:
    """URL 도메인·키워드·시간 기반 중요도 점수"""
    score = base

    # Tier 1 도메인 부스트
    for domain in TIER1_DOMAINS:
        if domain in url:
            score = max(score, 2.0)
            break

    # Tier 2 주요 기업/모델 언급 부스트
    title_lower = title.lower()
    for kw in TIER2_COMPANIES:
        if kw.lower() in title_lower:
            score = max(score, 1.5)
            break

    # 최신성 가점
    if pub:
        hours_ago = (datetime.now() - pub).total_seconds() / 3600
        if hours_ago <= 6:
            score += 0.5
        elif hours_ago <= 24:
            score += 0.2

    return round(score, 2)


# ── RSS 수집 (범용) ───────────────────────────────────────

def _parse_rss_feeds(
    feeds: list[tuple[str, str]],
    topic: str,
    hours: int = 72,
    base_score: float = 1.0,
    extra_headers: dict | None = None,
) -> list[TrendResult]:
    cutoff = datetime.now() - timedelta(hours=hours)
    keywords = _get_keywords(topic)
    results: list[TrendResult] = []
    seen: set[str] = set()

    for source, url in feeds:
        try:
            kwargs: dict = {}
            if extra_headers:
                kwargs["request_headers"] = extra_headers
            feed = feedparser.parse(url, **kwargs)

            for entry in feed.entries[:25]:
                link = getattr(entry, "link", "")
                if not link or link in seen:
                    continue

                # 날짜 파싱
                pub = None
                for attr in ("published_parsed", "updated_parsed"):
                    raw = getattr(entry, attr, None)
                    if raw:
                        try:
                            pub = datetime(*raw[:6])
                            break
                        except Exception:
                            pass

                if pub and pub < cutoff:
                    continue

                title = getattr(entry, "title", "").strip()
                summary = getattr(entry, "summary", "").strip()[:500]
                if not title:
                    continue

                # 키워드 필터 (제목·요약에 하나라도 포함)
                combined = (title + " " + summary).lower()
                if not any(k.lower() in combined for k in keywords):
                    continue

                seen.add(link)
                score = _calc_score(title, link, pub, base_score)

                # 영상 첨부(enclosure) 또는 YouTube 링크 포함 시 점수 부스트
                has_video = False
                enclosures = getattr(entry, "enclosures", [])
                for enc in enclosures:
                    if "video" in getattr(enc, "type", ""):
                        has_video = True
                        break
                if not has_video:
                    media_content = getattr(entry, "media_content", [])
                    for m in media_content:
                        if "video" in m.get("type", "") or "youtube" in m.get("url", ""):
                            has_video = True
                            break
                if not has_video:
                    # 링크 또는 summary에 youtube 포함
                    if "youtube.com" in link or "youtu.be" in link:
                        has_video = True
                    elif "youtube" in summary.lower() or "video" in summary.lower():
                        has_video = True
                if has_video:
                    score = round(score + 0.4, 2)

                results.append(TrendResult(title=title, url=link, content=summary, score=score))

        except Exception as e:
            print(f"  [TrendAnalyzer] RSS 실패 ({source}): {e}")

    return results


# ── Hacker News (무료 Algolia API) ───────────────────────

def _fetch_hacker_news(topic: str, hours: int = 48) -> list[TrendResult]:
    """HN Algolia Search API — 무료·인증 불필요, 기술 커뮤니티 실시간 반응"""
    cutoff_ts = int((datetime.now() - timedelta(hours=hours)).timestamp())
    keywords = _get_keywords(topic)
    # 영어 키워드만 HN 쿼리로 사용
    ascii_kws = [k for k in keywords if k.isascii() and len(k) > 1][:6]  # 단어 기준 최대 6개
    hn_query = " OR ".join(ascii_kws) or "AI"

    results: list[TrendResult] = []
    try:
        r = httpx.get(
            "https://hn.algolia.com/api/v1/search",
            params={
                "query": hn_query,
                "tags": "story",
                "numericFilters": f"created_at_i>{cutoff_ts},points>10",
                "hitsPerPage": 20,
            },
            timeout=15,
        )
        for hit in r.json().get("hits", []):
            title = hit.get("title", "")
            url = hit.get("url", "")
            if not url or not title:
                continue
            hn_pts = hit.get("points", 0)
            # HN 점수 → 중요도 정규화 (500점 이상 = 1.8)
            base = min(0.8 + hn_pts / 400, 1.8)
            score = _calc_score(title, url, None, base)
            results.append(TrendResult(
                title=title,
                url=url,
                content=hit.get("story_text", "")[:400] or title,
                score=score,
            ))
    except Exception as e:
        print(f"  [TrendAnalyzer] HackerNews 실패: {e}")
    return results


# ── Tavily 소셜 검색 (X/Twitter 등) ──────────────────────

def _tavily_social_search(topic: str) -> list[TrendResult]:
    """
    Tavily로 X(Twitter)·LinkedIn 등에 인덱싱된 AI 발표 수집.
    X API는 유료($100+/월)라 Tavily 검색으로 대체.
    """
    if not TAVILY_API_KEY:
        return []
    try:
        from tavily import TavilyClient
        client = TavilyClient(api_key=TAVILY_API_KEY)
        # 주요 AI 기관의 최신 발표에 집중
        query = (
            f"(OpenAI OR Anthropic OR Google DeepMind OR Meta AI OR Mistral) "
            f"{topic} announcement OR release OR launch"
        )
        resp = client.search(
            query=query,
            search_depth="basic",
            max_results=5,
            days=3,
        )
        results: list[TrendResult] = []
        for item in resp.get("results", []):
            title = item.get("title", "")
            url = item.get("url", "")
            if not title or not url:
                continue
            score = _calc_score(title, url, None, float(item.get("score", 0.5)))
            results.append(TrendResult(
                title=title,
                url=url,
                content=item.get("content", "")[:400],
                score=score,
            ))
        return results
    except Exception as e:
        print(f"  [TrendAnalyzer] Tavily social 실패: {e}")
        return []


# ── Tavily 뉴스 검색 (fallback) ───────────────────────────

def _tavily_news_search(topic: str, max_results: int = 5) -> list[TrendResult]:
    """Tavily news 검색 — 소스가 부족할 때 보조"""
    if not TAVILY_API_KEY:
        return []
    try:
        from tavily import TavilyClient
        client = TavilyClient(api_key=TAVILY_API_KEY)
        resp = client.search(
            query=topic,
            topic="news",
            search_depth="advanced",
            max_results=max_results,
            include_raw_content=True,
            days=7,
        )
        year = datetime.now().year
        results: list[TrendResult] = []
        for item in resp.get("results", []):
            title = item.get("title", "")
            years_in_title = re.findall(r"20\d{2}", title)
            if any(int(y) < year for y in years_in_title):
                continue
            content = (item.get("raw_content") or item.get("content") or "")[:3000]
            score = _calc_score(title, item.get("url", ""), None, float(item.get("score", 0.5)))
            results.append(TrendResult(
                title=title,
                url=item.get("url", ""),
                content=content,
                score=score,
            ))
        return results
    except Exception as e:
        print(f"  [TrendAnalyzer] Tavily 뉴스 실패: {e}")
        return []


# ── 원문 직접 크롤링 (httpx + BeautifulSoup) ─────────────

def _crawl_article(url: str, timeout: int = 10) -> str:
    """
    URL에서 기사 본문을 직접 크롤링.
    BeautifulSoup으로 본문 단락만 추출 (광고·메뉴 제거).
    실패 시 빈 문자열 반환.
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return ""

    SKIP_DOMAINS = {"reddit.com", "twitter.com", "x.com", "youtube.com"}
    if any(d in url for d in SKIP_DOMAINS):
        return ""

    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9,ko;q=0.8",
        }
        resp = httpx.get(url, headers=headers, timeout=timeout, follow_redirects=True)
        if resp.status_code != 200:
            return ""

        soup = BeautifulSoup(resp.text, "html.parser")

        # 불필요한 요소 제거
        for tag in soup(["script", "style", "nav", "header", "footer",
                          "aside", "figure", "figcaption", "form",
                          "button", "iframe", "noscript"]):
            tag.decompose()

        # 본문 후보: article > main > div.content 순으로 시도
        body = (
            soup.find("article")
            or soup.find("main")
            or soup.find(class_=lambda c: c and any(
                x in str(c).lower() for x in ["article", "post-body", "entry-content", "content"]
            ))
            or soup.body
        )
        if not body:
            return ""

        # p 태그 텍스트만 추출 (최소 30자 이상 단락만)
        paragraphs = [
            p.get_text(separator=" ", strip=True)
            for p in body.find_all("p")
            if len(p.get_text(strip=True)) >= 30
        ]
        text = "\n\n".join(paragraphs)

        # 최대 4000자
        return text[:4000]

    except Exception as e:
        print(f"  [TrendAnalyzer] 크롤링 실패 ({url[:50]}): {type(e).__name__}")
        return ""


def _enrich_article(article: TrendResult, min_length: int = 2000) -> TrendResult:
    """
    원문 크롤링 → Tavily extract 순으로 본문 보강.
    min_length 이상이면 유지. 기본 2000자 — RSS 500자 요약은 항상 보강 시도.
    """
    if len(article.content) >= min_length:
        return article

    print(f"  [TrendAnalyzer] 본문 보강 시도 (현재 {len(article.content)}자, 목표 {min_length}자+)...")

    # 1차: 직접 크롤링
    crawled = _crawl_article(article.url)
    if len(crawled) >= 500:
        print(f"  [TrendAnalyzer] 크롤링 완료: {len(crawled)}자")
        return TrendResult(
            title=article.title,
            url=article.url,
            content=crawled[:5000],
            score=article.score,
        )

    # 2차: Tavily extract fallback
    if not TAVILY_API_KEY:
        return article
    try:
        from tavily import TavilyClient
        client = TavilyClient(api_key=TAVILY_API_KEY)
        resp = client.extract(urls=[article.url])
        results = resp.get("results", [])
        if results and results[0].get("raw_content"):
            raw = results[0]["raw_content"]
            # 크롤링 결과보다 길면 Tavily 결과 사용
            content = raw[:5000] if len(raw) > len(crawled) else crawled[:5000]
            print(f"  [TrendAnalyzer] Tavily extract 완료: {len(content)}자")
            return TrendResult(
                title=article.title,
                url=article.url,
                content=content,
                score=article.score,
            )
    except Exception as e:
        print(f"  [TrendAnalyzer] Tavily extract 실패: {e}")

    # 기존 content + 크롤링 결과 합산 (둘 다 짧으면)
    if crawled:
        combined = article.content + "\n\n" + crawled
        return TrendResult(
            title=article.title, url=article.url,
            content=combined[:5000], score=article.score,
        )
    return article


# ── GPT 기사 선택 + 다중 기사 종합 ──────────────────────

def _pick_best_article(articles: list[TrendResult], topic: str) -> TrendResult:
    """
    GPT가 주 기사 1개 + 보조 기사 최대 2개를 선택.
    선택된 기사들을 모두 크롤링 후 본문을 종합해 최종 TrendResult 반환.
    """
    if len(articles) == 1:
        return _enrich_article(articles[0])

    numbered = "\n\n".join(
        f"[{i+1}] score={a.score:.1f} | 출처: {a.url[:55]}\n"
        f"제목: {a.title}\n내용: {a.content[:200]}"
        for i, a in enumerate(articles[:12])
    )

    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0, api_key=OPENAI_API_KEY)
    structured = llm.with_structured_output(_BestArticle)
    now = datetime.now()

    prompt = ChatPromptTemplate.from_messages([
        ("system",
         f"당신은 AI·개발자 커뮤니티용 인스타그램 카드뉴스 편집장입니다.\n"
         f"현재 날짜: {now.strftime('%Y년 %m월 %d일')}\n"
         f"타겟: AI 코딩·개발에 관심 있는 MZ세대 (개발자, 크리에이터)\n\n"
         f"=== ⚠️ 최우선 조건 — 주제 일치 ===\n"
         f"선택한 기사는 반드시 주제 '{topic}'을 직접 다루어야 한다.\n"
         f"주제에 포함된 기업명·제품명·기술명이 기사 제목이나 본문에 직접 언급될 것.\n"
         f"주제와 무관한 기사를 선택하면 즉시 오답 처리.\n\n"
         f"=== 선택 우선순위 (주제 일치 전제 하에) ===\n\n"
         f"1순위 ★★★ — 새 AI 모델·도구가 TODAY 출시·공개된 것:\n"
         f"  → 사용자가 오늘 바로 써볼 수 있는 것\n"
         f"  → HuggingFace 새 모델, Google AI 새 API, OpenAI 새 기능\n\n"
         f"2순위 ★★ — 기술적으로 신기하고 개발자가 흥미 있을 것\n\n"
         f"3순위 ★ — AI 산업 트렌드 (구체적 수치 있을 때만)\n\n"
         f"=== 반드시 피할 것 ===\n"
         f"  ✗ 주제 '{topic}'과 관련 없는 기사 (아무리 흥미로워도 선택 불가)\n"
         f"  ✗ 스타트업 서비스 출시 (API 사용 사례 ≠ 모델 출시)\n"
         f"  ✗ 투자·기업가치·매출·MOU·파트너십\n"
         f"  ✗ '전망', '예측', '결산', '리뷰', '돌아보기'\n"
         f"  ✗ {now.year - 1}년 이하 연도\n"
         f"  ✗ 사용 방법 안내·튜토리얼·가이드 페이지 (뉴스 X) — 예: 'Using X', 'How to use Y'\n"
         f"  ✗ 구체적 수치(숫자·%, 달러, 날짜)가 전혀 없는 기사\n\n"
         f"=== 가산점 ===\n"
         f"  ★ 기사에 구체적 수치/벤치마크 포함 (예: +30%, 1B tokens, $20/월, 300만 명)\n"
         f"  ★ 신제품·신기능 발표 — 독자가 오늘 바로 써볼 수 있는 것\n"
         f"  ★ 사회적 파장이 큰 사건 (소송, 규제, 인수합병 제외한 기술 충격)\n\n"
         f"index: 주 기사 번호 (1~N). 주제 일치 기사가 없으면 가장 유사한 것 선택\n"
         f"related_indices: 같은 주제를 다루는 보조 기사 번호 최대 2개 (없으면 빈 배열)\n"
         f"  → 같은 사건을 다른 각도에서 다룬 기사만 포함 (관련 없는 기사 제외)"
         ),
        ("human", f"주제: {topic}\n\n기사 목록:\n{numbered}"),
    ])

    main_idx = 0
    related_indices: list[int] = []
    try:
        result = (prompt | structured).invoke({})
        main_idx = max(0, min(result.index - 1, len(articles) - 1))
        related_indices = [
            max(0, min(i - 1, len(articles) - 1))
            for i in (result.related_indices or [])
            if i - 1 != main_idx
        ][:2]
        print(f"  [TrendAnalyzer] 선택: [{main_idx+1}] {articles[main_idx].title[:60]}")
        print(f"  [TrendAnalyzer] 이유: {result.reason}")
        if related_indices:
            print(f"  [TrendAnalyzer] 보조 기사: {[i+1 for i in related_indices]}")
    except Exception as e:
        print(f"  [TrendAnalyzer] 선택 실패 ({e}), 점수 최고값 사용")

    # ── 주 기사 크롤링 보강 ─────────────────────────────────
    main = _enrich_article(articles[main_idx])

    # ── 보조 기사 크롤링 + 종합 ────────────────────────────
    if not related_indices:
        return main

    supplementary_texts: list[str] = []
    for ri in related_indices:
        sup = _enrich_article(articles[ri])
        if len(sup.content) >= 200:
            supplementary_texts.append(
                f"[보조기사: {sup.title}]\n{sup.content[:1500]}"
            )

    if not supplementary_texts:
        return main

    # 주 기사 + 보조 기사 내용을 합쳐서 summary에 반영
    combined = (
        f"[주 기사: {main.title}]\n{main.content}\n\n"
        + "\n\n".join(supplementary_texts)
    )
    print(f"  [TrendAnalyzer] 다중 기사 종합: {len(combined)}자")

    return TrendResult(
        title=main.title,
        url=main.url,
        content=combined[:8000],   # 5000 → 8000: 보강된 전문 전달
        score=main.score,
    )


# ── 메인 ─────────────────────────────────────────────────

def run(topic: str, max_results: int = 7, ignored_titles: set | None = None) -> TrendReport:
    today = datetime.now()
    today_str = today.strftime("%Y년 %m월 %d일")
    print(f"  [TrendAnalyzer] {today_str} 기준 최신 뉴스 수집 중...")

    all_results: list[TrendResult] = []
    seen_urls: set[str] = set()

    def _add(items: list[TrendResult], label: str) -> None:
        new = [r for r in items if r.url and r.url not in seen_urls]
        for r in new:
            seen_urls.add(r.url)
        all_results.extend(new)
        print(f"  [TrendAnalyzer] {label}: {len(items)}건 수집 / {len(new)}건 신규")

    # ── 1. Tier 1 공식 AI 블로그 (72h) ──────────────────
    _add(_parse_rss_feeds(RSS_TIER1, topic, hours=72, base_score=2.0),
         "Tier1(공식 AI 블로그)")

    # ── 2. Hacker News (48h) ────────────────────────────
    _add(_fetch_hacker_news(topic, hours=48), "Tier2(Hacker News)")

    # ── 3. Reddit AI 서브레딧 RSS (48h) ─────────────────
    reddit_headers = {"User-Agent": "cardnews-algo/1.0"}
    _add(_parse_rss_feeds(REDDIT_RSS, topic, hours=48, base_score=1.3,
                          extra_headers=reddit_headers),
         "Tier2(Reddit)")

    # ── 4. 영문 IT 미디어 (72h) ──────────────────────────
    _add(_parse_rss_feeds(RSS_TIER3_EN, topic, hours=72, base_score=1.0),
         "Tier3(영문 미디어)")

    # ── 5. 국내 IT 미디어 (72h) ──────────────────────────
    _add(_parse_rss_feeds(RSS_TIER4_KR, topic, hours=72, base_score=0.8),
         "Tier4(국내 미디어)")

    # ── 6. Tavily 소셜 검색 (X/Twitter 등) ──────────────
    _add(_tavily_social_search(topic), "Social(Tavily/X)")

    # ── 7. Tavily fallback (소스 부족 시만) ─────────────
    if len(all_results) < 5:
        _add(_tavily_news_search(topic, max_results=5), "Tavily(fallback)")

    if not all_results:
        raise RuntimeError(f"'{topic}' 관련 최신 기사를 찾을 수 없습니다.")

    # ── 중복 방지: 최근 14일 발행 주제와 너무 유사한 기사 제거 ──
    try:
        from src.db import get_recent_topics
        recent_topics = get_recent_topics(days=14)
        if recent_topics:
            before = len(all_results)
            all_results = [
                r for r in all_results
                if not _is_duplicate_topic(r.title, recent_topics)
            ]
            removed = before - len(all_results)
            if removed > 0:
                print(f"  [TrendAnalyzer] 중복 제거: {removed}건 (최근 14일 유사 주제)")
    except Exception:
        pass

    if not all_results:
        raise RuntimeError(f"'{topic}' 관련 새로운 기사를 찾을 수 없습니다 (최근 14일 중복 제외).")

    # ── 소스 다양성 보장 후보 선별 ────────────────────────
    all_results.sort(key=lambda x: x.score, reverse=True)
    candidates = _select_diverse_candidates(all_results, n=12)

    # 파이프라인 재시도 시 이미 시도한 기사 제외 (영상 불가 등으로 탈락한 기사)
    if ignored_titles:
        before_ign = len(candidates)
        candidates = [c for c in candidates if c.title not in ignored_titles]
        removed_ign = before_ign - len(candidates)
        if removed_ign > 0:
            print(f"  [TrendAnalyzer] 이전 시도 기사 제외: {removed_ign}건")
        if not candidates:
            raise RuntimeError(f"'{topic}' 관련 새로운 기사가 없습니다 (모두 이전에 시도됨).")

    print(f"\n  [TrendAnalyzer] 총 {len(all_results)}건 후보 → 균형 선발 {len(candidates)}개:")
    for i, r in enumerate(candidates, 1):
        print(f"    [{i:02d}] score={r.score:.1f} | {r.title[:55]}")

    # GPT 최적 기사 선택
    best = _pick_best_article(candidates, topic)

    # 본문이 짧으면 직접 크롤링 → Tavily extract 순으로 보강
    if len(best.content) < 500:
        print(f"  [TrendAnalyzer] 본문 보강 중... (현재 {len(best.content)}자)")
        best = _enrich_article(best)

    print(f"  [TrendAnalyzer] 최종 선택: {best.title}")
    print(f"  [TrendAnalyzer] 본문 길이: {len(best.content)}자")

    summary = (
        f"## 선택된 기사 ({today_str})\n\n"
        f"★ 기사 제목 (커버에 이 제목을 다듬어서 사용): {best.title}\n"
        f"출처: {best.url}\n\n"
        f"기사 본문:\n{best.content}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"[카드뉴스 작성 규칙 — 반드시 준수]\n\n"
        f"1. 커버(1장): 기사 제목을 20자 이내로 압축. 숫자나 구체적 사실 포함 필수\n\n"
        f"2. 본문(2~5장): 각 슬라이드마다 기사에서 직접 뽑은 사실 1가지씩\n"
        f"   ✅ 필수: 수치(%, 배수, 달러, 날짜), 기업명, 인물 이름, 발표 내용\n"
        f"   ✅ 예시: '컨텍스트 윈도우 1M 토큰' / 'API 가격 $0.075/1M tokens'\n"
        f"   ❌ 금지: '~전망', '~예상', '~할 수 있다', '중요하다', '주목받고 있다'\n"
        f"   ❌ 금지: 기사에 없는 내용 추가, 추측, 배경 설명\n\n"
        f"3. 출처 명시: body 첫 줄에 '출처: [기사제목 or 기관명]' 추가 (커버 제외)\n\n"
        f"4. 수치가 기사에 없을 경우: body에 '(수치 미기재)'라고 명시하고 정성적 사실만 작성\n"
    )

    # 데모/튜토리얼 영상 검색 키워드
    # 기사 제목이 구체적이면 제목 기반, generic하면 원래 topic 기반
    clean_title = re.sub(r'^\[(HN|Reddit[^\]]*)\]\s*', '', best.title).strip()
    generic_words = ["latest", "announced", "news", "monthly", "weekly", "update", "review"]
    title_is_generic = sum(1 for w in generic_words if w in clean_title.lower()) >= 2
    yt_base = topic if title_is_generic else clean_title[:40]
    yt_keyword = f"site:youtube.com \"{yt_base}\" demo {today.year}"

    # 선택된 best 기사를 results[0]에 배치 → pipeline raw_article_body가 올바른 원문을 받도록
    other_results = [r for r in all_results if r.url != best.url]
    ordered_results = [best] + other_results[:max_results - 1]

    return TrendReport(
        query=topic,
        results=ordered_results,
        summary=summary,
        youtube_keyword=yt_keyword,
    )
