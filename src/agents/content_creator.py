"""
Phase 2: Content Creator — 2단계 방식 (Evidence-bound Claim 기반 개편)
Step 1: 기사에서 구체적 사실(Claim) 추출
Step 2: 추출된 사실 결정적/의미적 검증 (Quality Gate)
Step 3: 통과된 사실만으로 카드뉴스 생성
→ GPT가 기사 내용을 무시하고 임의로 만드는 것을 완전 차단
"""
from __future__ import annotations

from typing import Optional

from src.schemas.card_news import CardNewsScript, TrendReport, SourceLineage
from src.persona import load_persona, Persona
from src.qa.claim_generator import ClaimGenerator
from src.qa.deterministic_verifier import DeterministicVerifier, QualityGateError
from src.qa.semantic_critic import run_semantic_critic
from src.qa.script_assembler import ScriptAssembler


def _is_listicle_topic(topic: str) -> bool:
    import re
    return bool(re.search(r'\d+가지|\d+대|TOP\s*\d+|탑\s*\d+|\d+선|이유\s*\d+', topic, re.IGNORECASE))


class ContentCreator:
    """Legacy adapter for Pipeline integration"""

    def __init__(self, brand_persona: Persona | None = None):
        self.persona = brand_persona or load_persona()
        self.claim_generator = ClaimGenerator()

    def run(
        self,
        topic: str,
        trend_report: TrendReport,
        num_cards: Optional[int] = None,
        handle: str = "algo__kr",
        persona: Optional[Persona] = None,
        video_infos: Optional[list] = None,
        feedback: str = "",
        raw_article_body: str = "",
        disputed_notes: str = "",
        source_lineage: Optional[SourceLineage] = None,
    ) -> CardNewsScript:
        """
        새로운 증거 기반 Claim 생성 및 검증을 수행한 뒤 ScriptAssembler로 넘깁니다.
        """
        # 1. Lineage 확인 (신규 생성 시 V2 필수)
        if not source_lineage or not source_lineage.is_verified_ready:
            raise QualityGateError("LEGACY_LINEAGE_UNVERIFIED", "Cannot generate new content with unverified legacy source lineage.")

        # 2. Claim 생성
        claims = self.claim_generator.generate_claims(source_lineage)

        # 3. Quality Gate (Deterministic)
        DeterministicVerifier.verify_claims(claims, source_lineage)

        # 4. Quality Gate (Semantic)
        run_semantic_critic(claims, source_lineage)

        # 5. Script Assemble
        script = ScriptAssembler.assemble(topic=source_lineage.topic, claims=claims)

        return script

def run(
    topic: str,
    trend_report: Optional[TrendReport] = None,
    num_cards: int = 5,
    handle: str = "algo__kr",
    persona: Optional[Persona] = None,
    video_infos: Optional[list] = None,
    feedback: str = "",
    raw_article_body: str = "",
    disputed_notes: str = ""
) -> CardNewsScript:
    import warnings
    warnings.warn("content_creator.run function is deprecated. Instantiate ContentCreator and call run() instead.", DeprecationWarning, stacklevel=2)
    return ContentCreator().run(
        topic=topic,
        trend_report=trend_report,
        num_cards=num_cards,
        handle=handle,
        persona=persona,
        video_infos=video_infos,
        feedback=feedback,
        raw_article_body=raw_article_body,
        disputed_notes=disputed_notes,
        source_lineage=None
    )
