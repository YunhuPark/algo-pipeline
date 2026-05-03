"""
주제 정제 에이전트 (Topic Refiner)
─────────────────────────────────────────────────────────
"인공지능 에이전트 최신 트렌드" 같은 넓고 모호한 주제를
"AMD GAIA — 채팅 한 줄로 만드는 AI 에이전트 데스크톱 앱" 처럼
하나의 기사·사건에 집중된 구체 주제로 변환한다.

작동 원리:
  1) 주제가 충분히 구체적이면 그대로 반환 (Tavily 검색 불필요)
  2) 모호/광범위 → Tavily로 관련 최신 기사 3건 빠르게 검색
  3) GPT-4o-mini가 검색 결과에서 가장 독립된 핵심 기사 1건 선택
  4) 선택된 기사 제목 기반으로 정제된 주제 문자열 반환
"""
from __future__ import annotations

import json
import re
from datetime import datetime

_BROAD_SIGNALS = [
    "트렌드", "최신", "동향", "현황", "분석", "총정리", "정리",
    "리뷰", "개요", "전망", "이슈", "뉴스", "이번주", "요즘",
    "trend", "latest", "overview", "roundup", "summary", "top",
    "best", "news", "update", "weekly", "monthly",
]

_SPECIFIC_SCORE_BOOST = [
    r"\d{4}",           # 연도 포함
    r"[A-Z]{2,}",       # 대문자 약어 (GPT, AMD, GAIA…)
    r"\d+\.\d+",        # 버전 번호 (GPT-4o, iOS 18.1…)
    r"발표|출시|공개|상장|인수|합병|차단|승인|규제",  # 구체 사건
]


def _is_broad(topic: str) -> bool:
    """주제가 광범위/모호한지 판단"""
    t = topic.lower()
    # 광범위 시그널 포함 여부
    has_broad = any(s in t for s in _BROAD_SIGNALS)
    # 구체성 점수 (정규식 패턴이 1개 이상 매칭 → 구체적)
    specificity = sum(1 for p in _SPECIFIC_SCORE_BOOST if re.search(p, topic))
    # 길이도 참고: 10자 미만은 거의 항상 모호
    too_short = len(topic.strip()) < 10
    return (has_broad and specificity < 2) or too_short


def refine_topic(topic: str) -> tuple[str, str, str]:
    """
    광범위한 주제를 구체 기사 한 건에 집중된 주제로 정제.

    반환:
      (refined_topic, reason, article_content)
      - refined_topic: 정제된 주제 문자열 (변경 없으면 원본 그대로)
      - reason: 정제 이유 또는 "이미 구체적"
      - article_content: 선택된 기사 본문 (없으면 빈 문자열) → trend_context로 활용
    """
    if not _is_broad(topic):
        return topic, "이미 구체적인 주제 — 정제 불필요", ""

    print(f"  [TopicRefiner] 광범위한 주제 감지 → 최신 기사 검색: '{topic}'")

    try:
        from src.config import TAVILY_API_KEY, OPENAI_API_KEY
        from tavily import TavilyClient
        from langchain_openai import ChatOpenAI

        # 1) 최신 기사 3건 검색
        client = TavilyClient(api_key=TAVILY_API_KEY)
        results = client.search(
            topic,
            search_depth="basic",
            max_results=5,
            days=7,   # 7일 이내 최신 기사만
        )
        items = results.get("results", [])[:5]

        if not items:
            print(f"  [TopicRefiner] 검색 결과 없음 → 원본 주제 유지")
            return topic, "검색 결과 없음", ""

        # 검색 결과 텍스트화
        arts_text = "\n".join(
            f"[{i+1}] 제목: {r.get('title','')}\n    요약: {r.get('content','')[:200]}"
            for i, r in enumerate(items)
        )

        # 2) GPT로 커버할 기사 1건 선택 + 정제 주제 생성
        llm = ChatOpenAI(model="gpt-4o-mini", temperature=0, api_key=OPENAI_API_KEY)
        year = datetime.now().year

        prompt = (
            f"사용자가 입력한 주제: '{topic}'\n\n"
            f"최신 관련 기사 목록:\n{arts_text}\n\n"
            f"지시사항:\n"
            f"1. 위 기사 목록에서 가장 독립적이고 임팩트 있는 기사 1건을 선택하세요.\n"
            f"2. 그 기사를 커버하는 카드뉴스 주제를 한국어로 작성하세요.\n"
            f"   - 기업명·제품명·수치 중 하나 이상 포함 (예: 'AMD GAIA의 AI 에이전트 노코드 생성')\n"
            f"   - 광범위한 표현('최신 트렌드', '요즘 AI' 등) 절대 사용 금지\n"
            f"   - 20자 이내로 간결하게\n"
            f"3. 선택한 기사 번호와 정제된 주제를 JSON으로 출력:\n"
            f"   {{\"selected\": 번호, \"refined_topic\": \"...\", \"reason\": \"선택 이유 한 줄\"}}"
        )

        result = llm.invoke(prompt)
        raw = re.sub(r"```[a-z]*\n?", "", result.content.strip()).strip("`")
        data = json.loads(raw)

        refined = data.get("refined_topic", "").strip()
        reason = data.get("reason", "")
        selected_idx = int(data.get("selected", 1)) - 1

        if not refined:
            return topic, "GPT 정제 결과 없음", ""

        # 선택된 기사 제목 + 본문 추출 (trend_context로 바로 사용 가능하게)
        selected_article_content = ""
        if 0 <= selected_idx < len(items):
            sel = items[selected_idx]
            sel_title = sel.get("title", "")[:60]
            sel_content = sel.get("content", "")
            sel_url = sel.get("url", "")
            print(f"  [TopicRefiner] 선택 기사: [{selected_idx+1}] '{sel_title}'")
            # Tavily extract로 전문 가져오기 시도
            try:
                extract_results = client.extract(urls=[sel_url])
                if extract_results and extract_results.get("results"):
                    full_text = extract_results["results"][0].get("raw_content", "")
                    if len(full_text) > 1000:
                        selected_article_content = f"[기사 제목: {sel_title}]\n[출처: {sel_url}]\n\n{full_text[:5000]}"
                        print(f"  [TopicRefiner] 기사 전문 수집: {len(full_text)}자")
            except Exception:
                pass
            # 전문 수집 실패 시 스니펫만이라도 사용
            if not selected_article_content and sel_content:
                selected_article_content = f"[기사 제목: {sel_title}]\n[출처: {sel_url}]\n\n{sel_content}"

        print(f"  [TopicRefiner] '{topic}' → '{refined}' (이유: {reason})")
        return refined, reason, selected_article_content

    except Exception as e:
        print(f"  [TopicRefiner] 정제 실패 ({e}) → 원본 유지")
        return topic, f"오류: {e}", ""
