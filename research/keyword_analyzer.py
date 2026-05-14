"""트렌드 + 뉴스 데이터 → 핵심 테마 추출"""
from __future__ import annotations

from collections import Counter

from content.models import Theme


def analyze(
    trends_data: list[dict],
    news_data: list[dict],
    max_themes: int = 5,
) -> list[dict]:
    """
    트렌드와 뉴스 데이터를 결합해 카드뉴스에 담을 핵심 테마를 반환한다.
    Claude 없이 순수 로컬 로직으로 처리.
    """
    # 키워드 빈도 집계
    keyword_freq: Counter = Counter()

    for item in trends_data:
        kw = item.get("keyword", "")
        score = item.get("score", 50)
        if kw:
            keyword_freq[kw] += score

    # 뉴스 제목에서 명사구 추출 (간단한 규칙 기반)
    for article in news_data:
        title = article.get("title", "")
        summary = article.get("summary", "")
        for text in [title, summary]:
            # 따옴표 안 키워드, 특수문자 제거 후 2~8자 단어 추출
            import re
            words = re.findall(r"[가-힣A-Za-z0-9]{2,8}", text)
            for w in words:
                keyword_freq[w] += 1

    # 상위 키워드로 테마 구성
    top_keywords = [kw for kw, _ in keyword_freq.most_common(30)]

    # 테마 구성: 관련 키워드끼리 묶기 (간단한 prefix 그룹핑)
    themes: list[dict] = []
    used: set[str] = set()

    for kw in top_keywords:
        if kw in used or len(themes) >= max_themes:
            break

        # 이 키워드와 prefix 공유하는 관련어 찾기
        related = [k for k in top_keywords if k != kw and (kw[:2] in k or k[:2] in kw) and k not in used][:3]
        used.add(kw)
        used.update(related)

        theme = Theme(
            name=kw,
            description=f"{kw} 관련 최신 동향",
            supporting_keywords=[kw] + related,
            angle_suggestion=f"'{kw}'의 현재 상황과 앞으로의 전망",
        )
        themes.append(theme.model_dump())

    # 테마가 너무 적으면 뉴스 제목을 직접 테마로 활용
    if len(themes) < max_themes:
        for article in news_data[: max_themes - len(themes)]:
            title = article.get("title", "")
            if title and title not in used:
                theme = Theme(
                    name=title[:20],
                    description=title,
                    supporting_keywords=[title[:10]],
                    angle_suggestion=title,
                )
                themes.append(theme.model_dump())

    return themes[:max_themes]
