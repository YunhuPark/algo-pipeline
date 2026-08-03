"""카드뉴스 파이프라인 전체에서 사용하는 Pydantic 스키마"""
from __future__ import annotations

from typing import Literal, Optional, List

from pydantic import BaseModel, Field, model_validator


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

from pydantic import BaseModel, Field, model_validator, AnyHttpUrl
from decimal import Decimal

# ... (keep Slide, CardNewsScript, TrendResult)

class NormalizedNumber(BaseModel):
    raw_text: str
    normalized_value: Decimal
    unit: str
    qualifier: str = ""
    subject: str = ""

class NormalizedDate(BaseModel):
    raw_text: str
    normalized_date: str
    precision: str
    is_relative: bool
    reference_date: str = ""

class EvidencePassage(BaseModel):
    """원문 근거 구절"""
    evidence_id: str = Field(..., min_length=1)
    article_id: str = Field(..., min_length=1)
    text: str = Field(..., min_length=1)
    source_url: str = Field(..., min_length=1)
    location: Optional[str] = ""
    content_hash: str = Field(..., min_length=1)

    @model_validator(mode='after')
    def validate_url(self) -> 'EvidencePassage':
        if not self.source_url.startswith('http'):
            raise ValueError("source_url must be a valid URL starting with http")
        return self


class Claim(BaseModel):
    """LLM이 생성한 독립적인 사실/주장 단위"""
    claim_id: str = Field(..., min_length=1)
    claim_text: str = Field(..., min_length=1)
    claim_type: Literal["factual", "numerical", "attributed_statement", "inference", "opinion", "cta"]
    entities: List[str] = Field(default_factory=list)
    numbers: List[NormalizedNumber] = Field(default_factory=list)
    dates: List[NormalizedDate] = Field(default_factory=list)
    evidence_ids: List[str] = Field(default_factory=list)
    source_url: str = ""
    verification_status: Literal["pending", "verified", "disputed", "unverifiable"] = "pending"
    verification_reason: str = ""


class SemanticCriticResult(BaseModel):
    """의미론적 검증 결과"""
    claim_id: str
    verdict: Literal["supported", "contradicted", "insufficient_evidence"]
    evidence_ids: List[str]
    reason: str = Field(..., min_length=1)
    confidence: float

    @model_validator(mode='after')
    def validate_confidence(self) -> 'SemanticCriticResult':
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError("confidence must be between 0.0 and 1.0")
        return self



class SourceLineage(BaseModel):
    """뉴스 수집 결과의 Topic, 출처, 컨텍스트를 하나로 묶은 식별자"""
    schema_version: str = "1.0"
    topic: str
    source_title: str
    source_url: str
    context: str
    article_id: str = ""

    # 신규 필드 (v2.0)
    published_at: str = ""
    retrieved_at: str = ""
    content_hash: str = ""
    collection_method: str = ""
    source_material_level: Literal["full_article", "partial_article", "snippet_only", "unknown"] = "unknown"
    evidence_passages: List[EvidencePassage] = Field(default_factory=list)

    @model_validator(mode='after')
    def validate_schema_version(self) -> 'SourceLineage':
        if self.schema_version >= "2.0":
            if not self.article_id:
                raise ValueError("article_id is required for schema_version >= 2.0")
            if not self.source_url:
                raise ValueError("source_url is required for schema_version >= 2.0")
            if not self.content_hash:
                raise ValueError("content_hash is required for schema_version >= 2.0")
            
            # Evidence unique id check
            seen_ids = set()
            for ev in self.evidence_passages:
                if ev.evidence_id in seen_ids:
                    raise ValueError(f"Duplicate evidence_id: {ev.evidence_id}")
                seen_ids.add(ev.evidence_id)
                if ev.article_id != self.article_id:
                    raise ValueError(f"Evidence article_id {ev.article_id} mismatch with lineage {self.article_id}")
                if ev.source_url != self.source_url:
                    raise ValueError(f"Evidence source_url {ev.source_url} mismatch with lineage {self.source_url}")
                
        return self

    @property
    def is_verified_ready(self) -> bool:
        return self.schema_version >= "2.0"

class TrendReport(BaseModel):
    """Trend Analyzer가 반환하는 최종 분석 보고서"""
    query: str
    results: list[TrendResult]
    summary: str = ""              # 수집된 원문 요약 (Content Creator에 주입용)
    youtube_keyword: str = ""      # 유튜브 썸네일 검색 키워드
