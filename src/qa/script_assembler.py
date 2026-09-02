from typing import List
from src.schemas.card_news import Claim, CardNewsScript, Slide
import uuid

class ScriptAssembler:
    """
    Assembles a CardNewsScript strictly from verified claims without invoking the LLM to rewrite them,
    preventing re-hallucination.
    """

    @staticmethod
    def assemble(topic: str, claims: List[Claim]) -> CardNewsScript:
        verified_claims = [c for c in claims if c.verification_status == "verified"]

        # We need at least some content
        if not verified_claims:
            raise ValueError("No verified claims available to assemble script.")

        slides = []

        # 1. Cover slide
        slides.append(Slide(
            slide_number=1,
            slide_type="cover",
            title=f"{topic} 요약",
            body="최신 핵심 정보를 정리해 드립니다.",
            emoji="📰"
        ))

        # 2. Content slides
        content_claims = [c for c in verified_claims if c.claim_type != "cta"]
        # Limit to 4 content slides to match 1 cover + 4 content + 1 cta = 6 max if we want
        for i, c in enumerate(content_claims[:4]):
            accent = ""
            if c.numbers:
                accent = c.numbers[0]
            elif c.entities:
                accent = c.entities[0]

            slides.append(Slide(
                slide_number=len(slides) + 1,
                slide_type="content",
                title=f"핵심 포인트 {i+1}",
                body=c.claim_text,
                accent=accent
            ))

        # 3. CTA slide
        cta_claims = [c for c in verified_claims if c.claim_type == "cta"]
        cta_body = cta_claims[0].claim_text if cta_claims else "더 자세한 내용은 원문을 참고해 주세요."

        slides.append(Slide(
            slide_number=len(slides) + 1,
            slide_type="cta",
            title="여러분의 생각은?",
            body=cta_body,
            emoji="👇"
        ))

        # Ensure exact requirements for CardNewsScript
        return CardNewsScript(
            topic=topic,
            hook=f"{topic}의 모든 것",
            slides=slides,
            hashtags=["#뉴스", "#정보"]
        )
