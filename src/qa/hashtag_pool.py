"""Hashtag choices for the cards: broad AI tags people actually search, plus tags that fit the post.

The long "whole headline" tag the assembler used to add reached nobody, so tags now come from:
  1. the brand and the format (#알고, #카드뉴스),
  2. the companies/products the cards mention (short, searchable names),
  3. topic tags picked by keywords found in the article (see KEYWORD_TAGS),
  4. broad AI tags to fill the rest.

The pools below are a hand-picked list of widely used Korean tech/AI tags. They are NOT live
popularity data: Instagram's hashtag-search API is not available to this account, so nothing here is
measured. Edit the lists when you learn which tags perform; everything else follows.
"""
from __future__ import annotations

import re

BRAND_TAGS = ["#알고", "#카드뉴스"]

# Broad tags used to fill the remaining slots (most useful first).
BROAD_AI_TAGS = ["#AI뉴스", "#인공지능", "#AI트렌드", "#테크뉴스", "#IT뉴스", "#생성형AI"]

# keyword (matched case-insensitively against topic, card text and entities) -> tags
KEYWORD_TAGS: list[tuple[tuple[str, ...], list[str]]] = [
    (("챗gpt", "chatgpt", "gpt", "오픈ai", "openai"), ["#챗GPT", "#오픈AI"]),
    (("제미나이", "gemini", "구글", "google"), ["#구글", "#제미나이"]),
    (("클로드", "claude", "앤트로픽", "anthropic"), ["#클로드", "#앤트로픽"]),
    (("엔비디아", "nvidia", "gpu", "그래픽카드"), ["#엔비디아", "#반도체"]),
    (("반도체", "칩", "chip", "hbm"), ["#반도체"]),
    (("마이크로소프트", "microsoft", "코파일럿", "copilot"), ["#마이크로소프트"]),
    (("애플", "apple", "아이폰", "iphone", "시리", "siri"), ["#애플"]),
    (("메타", "meta", "라마", "llama"), ["#메타"]),
    (("삼성", "samsung"), ["#삼성전자"]),
    (("광고", "마케팅"), ["#디지털마케팅", "#광고"]),
    (("투자", "기업가치", "ipo", "유치", "시리즈"), ["#AI투자", "#스타트업"]),
    (("보안", "해킹", "사이버"), ["#사이버보안"]),
    (("로봇", "robot"), ["#로봇"]),
    (("규제", "법안", "정책"), ["#AI규제"]),
    (("에이전트", "agent"), ["#AI에이전트"]),
    (("영상", "비디오", "video", "이미지 생성", "생성형"), ["#생성형AI"]),
    (("금융", "은행"), ["#핀테크"]),
]

# Too broad to help discovery (countries/regions) or not a name worth tagging.
_SKIP_ENTITIES = {
    "미국", "중국", "한국", "일본", "유럽", "중남미", "아시아", "인도", "영국", "독일", "프랑스",
    "us", "usa", "uk", "eu", "ai", "ceo",
}
_PERSON_NAME = re.compile(r"[A-Z][a-z]+ [A-Z][a-z]+")


def _tag(word: str) -> str:
    return "#" + re.sub(r"[^0-9A-Za-z가-힣_]", "", word)


def entity_tags(entities: list[str], limit: int = 3) -> list[str]:
    """Short, searchable names (companies/products). People's names and broad places are skipped."""
    tags: list[str] = []
    for entity in entities:
        entity = entity.strip()
        # an English person name has been tagged by mistake before (an article's author)
        if not entity or _PERSON_NAME.fullmatch(entity):
            continue
        tag = _tag(entity)
        body = tag[1:]
        if len(body) < 2 or len(body) > 12 or body.isdigit() or body.lower() in _SKIP_ENTITIES:
            continue
        if tag.lower() not in {t.lower() for t in tags}:
            tags.append(tag)
        if len(tags) == limit:
            break
    return tags


def keyword_tags(text: str, limit: int = 3) -> list[str]:
    """Topic tags for the keywords found in the article text, in the order of KEYWORD_TAGS."""
    haystack = text.lower()
    tags: list[str] = []
    for keywords, candidates in KEYWORD_TAGS:
        if any(k in haystack for k in keywords):
            for tag in candidates:
                if tag not in tags:
                    tags.append(tag)
        if len(tags) >= limit:
            break
    return tags[:limit]


def build_hashtags(topic: str, claim_text: str, entities: list[str], total: int = 10) -> list[str]:
    """Brand + format, then entity tags, keyword tags and broad AI tags, without duplicates."""
    chosen: list[str] = []
    seen: set[str] = set()

    def add(tags: list[str]) -> None:
        for tag in tags:
            key = tag.lower()
            if key not in seen and len(chosen) < total:
                seen.add(key)
                chosen.append(tag)

    add(BRAND_TAGS)
    add(entity_tags(entities))
    add(keyword_tags(f"{topic} {claim_text} {' '.join(entities)}"))
    add(BROAD_AI_TAGS)
    return chosen
