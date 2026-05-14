"""네이버 뉴스 스크래퍼 (httpx + BeautifulSoup)"""
from __future__ import annotations

import time

import httpx
from bs4 import BeautifulSoup

from content.models import NewsItem

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9",
}


def get_recent_articles(query: str, max_results: int = 10) -> list[dict]:
    """네이버 뉴스 검색 결과를 수집한다."""
    url = "https://search.naver.com/search.naver"
    params = {
        "where": "news",
        "query": query,
        "sort": "1",  # 최신순
        "pd": "4",    # 1주일
    }

    try:
        with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=10) as client:
            resp = client.get(url, params=params)
            resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        articles = []

        for item in soup.select(".news_tit"):
            title = item.get_text(strip=True)
            link = item.get("href", "")

            # 요약
            parent = item.find_parent("div", class_="news_info") or item.find_parent("li")
            summary = ""
            if parent:
                desc = parent.find(class_="news_dsc") or parent.find(class_="dsc_txt_wrap")
                if desc:
                    summary = desc.get_text(strip=True)

            # 날짜
            date_tag = parent.find(class_="info_group") if parent else None
            published = ""
            if date_tag:
                spans = date_tag.find_all("span")
                for span in spans:
                    t = span.get_text(strip=True)
                    if "전" in t or "일" in t or "시간" in t or "분" in t:
                        published = t
                        break

            articles.append(
                NewsItem(title=title, summary=summary, url=link, published_date=published).model_dump()
            )

            if len(articles) >= max_results:
                break

        time.sleep(0.5)
        return articles

    except Exception as exc:  # 스크래핑 실패시 빈 리스트 반환
        return [{"title": f"[수집 실패: {exc}]", "summary": "", "url": "", "published_date": ""}]
