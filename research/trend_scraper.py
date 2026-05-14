"""Google Trends KR 트렌드 스크래퍼 (Playwright 기반, httpx 폴백)"""
from __future__ import annotations

import json
import time
import urllib.parse

import httpx

from content.models import TrendItem

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9",
}


def _fetch_via_playwright(topic: str, timeframe: str = "today 7-d") -> list[dict]:
    """Playwright로 Google Trends 관련 검색어를 스크래핑한다."""
    try:
        from playwright.sync_api import sync_playwright

        encoded = urllib.parse.quote(topic)
        url = f"https://trends.google.com/trends/explore?q={encoded}&geo=KR&date={urllib.parse.quote(timeframe)}&hl=ko"

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(extra_http_headers={"Accept-Language": "ko-KR,ko;q=0.9"})
            page.goto(url, timeout=20000)
            page.wait_for_timeout(4000)

            items: list[dict] = []

            # 관련 검색어 테이블
            rows = page.query_selector_all(".fe-related-searches .item")
            for row in rows[:10]:
                keyword_el = row.query_selector(".label-text")
                score_el = row.query_selector(".value")
                if keyword_el:
                    keyword = keyword_el.inner_text().strip()
                    score_text = score_el.inner_text().strip() if score_el else "0"
                    is_rising = "+" in score_text or "급상승" in score_text
                    score_text = score_text.replace("+", "").replace("%", "").replace(",", "").strip()
                    try:
                        score = int(score_text)
                    except ValueError:
                        score = 50
                    items.append(TrendItem(keyword=keyword, score=score, is_rising=is_rising).model_dump())

            browser.close()
            return items if items else []

    except Exception:
        return []


def _fetch_via_naver_datalab(topic: str) -> list[dict]:
    """네이버 데이터랩 검색어 트렌드 API (API 키 불필요한 공개 엔드포인트)"""
    try:
        url = "https://datalab.naver.com/keyword/trendSearch.naver"
        payload = {
            "startDate": "",
            "endDate": "",
            "timeUnit": "date",
            "keyword": [{"name": topic, "param": [topic]}],
            "device": "",
            "ages": [],
            "gender": "",
        }
        with httpx.Client(headers=HEADERS, timeout=10) as client:
            resp = client.post(url, json=payload)
            data = resp.json()

        results = data.get("results", [])
        if not results:
            return []

        items = []
        for point in results[0].get("data", [])[-7:]:
            items.append(TrendItem(keyword=topic, score=int(point.get("ratio", 0)), is_rising=False).model_dump())

        return items
    except Exception:
        return []


def _fetch_related_via_google_suggest(topic: str) -> list[dict]:
    """Google 자동완성 API로 관련 검색어 수집 (폴백)"""
    try:
        url = "https://suggestqueries.google.com/complete/search"
        params = {"client": "firefox", "hl": "ko", "gl": "kr", "q": topic}
        with httpx.Client(headers=HEADERS, timeout=8) as client:
            resp = client.get(url, params=params)
        suggestions = resp.json()[1]
        return [
            TrendItem(keyword=s, score=50, is_rising=False).model_dump()
            for s in suggestions[:10]
            if s != topic
        ]
    except Exception:
        return []


def get_weekly_trends(topic: str, timeframe: str = "today 7-d") -> list[dict]:
    """
    주어진 주제의 Google Trends KR 인기 키워드를 반환한다.
    Playwright → Naver DataLab → Google Suggest 순으로 시도.
    """
    items = _fetch_via_playwright(topic, timeframe)
    if items:
        return items

    items = _fetch_related_via_google_suggest(topic)
    if items:
        return items

    # 최후 폴백: 주제 자체를 트렌드 아이템으로 반환
    return [TrendItem(keyword=topic, score=50, is_rising=True).model_dump()]
