"""카드뉴스 파이프라인 전체에서 사용하는 Pydantic 스키마"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Slide(BaseModel):
    """카드뉴스 슬라이드 1장"""
    slide_number: int = Field(..., description="슬라이드 순번 (1부터 시작)")
    slide_type: Literal["cover", "content", "cta"] = Field(
        ..., description="슬라이드 역할: 표지/본문/마무리CTA"
    )
    title: str = Field(..., description="굵은 헤드라인 (최대 22자, 이모지 금지)")
    body: str = Field(..., description="본문 설명 텍스트 (60자 이상 130자 이하, 줄바꿈 \\n 허용, 사람이 쓴 것처럼 자연스럽게)")
    emoji: str = Field(default="", description="장식 이모지 1개 (없으면 빈 문자열)")
    accent: str = Field(default="", description="강조 수치·인용 (예: '73% 증가', 15자 이내, 없으면 빈 문자열)")


class CardNewsScript(BaseModel):
    """GPT-4o가 반환하는 전체 카드뉴스 스크립트"""
    topic: str = Field(..., description="카드뉴스 주제")
    hook: str = Field(..., description="인스타그램 캡션 첫 줄 (후킹 문구, 최대 30자)")
    slides: list[Slide] = Field(..., description="슬라이드 목록 (표지 1 + 본문 N + CTA 1)")
    hashtags: list[str] = Field(..., description="15~20개 해시태그 (#포함)")

    @property
    def cover(self) -> Slide:
        return next(s for s in self.slides if s.slide_type == "cover")

    @property
    def content_slides(self) -> list[Slide]:
        return [s for s in self.slides if s.slide_type == "content"]

    @property
    def cta(self) -> Slide:
        return next(s for s in self.slides if s.slide_type == "cta")


class TrendResult(BaseModel):
    """Tavily 검색 결과 1건"""
    title: str
    url: str
    content: str
    score: float = 0.0


class TrendReport(BaseModel):
    """Trend Analyzer가 반환하는 최종 분석 보고서"""
    query: str
    results: list[TrendResult]
    summary: str = ""              # 수집된 원문 요약 (Content Creator에 주입용)
    youtube_keyword: str = ""      # 유튜브 썸네일 검색 키워드
