import json
from typing import List, Dict, Optional
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from src.schemas.card_news import Claim, SourceLineage, SemanticCriticResult
from src.qa.deterministic_verifier import QualityGateError

_CRITIC_SYSTEM_PROMPT = """
당신은 엄격한 사실 검증(Semantic Critic) AI입니다.
주어진 원문(Evidence)과 생성된 주장(Claim)을 비교하여, 주장이 원문에 의해 완전히 지지되는지(supported),
모순되는지(contradicted), 혹은 원문 내용만으로는 판단할 수 없는지(insufficient_evidence) 판정합니다.

결과는 반드시 JSON으로 응답해야 하며, 다음 스키마를 따르십시오.
{{
    "verdict": "supported" | "contradicted" | "insufficient_evidence",
    "reason": "판단 근거를 1~2문장으로 간략히 설명",
    "confidence": 0.0 ~ 1.0 사이의 실수
}}

규칙:
1. 원문에 명시적으로 언급되지 않은 추론은 insufficient_evidence로 처리합니다.
2. 긍정/부정이 반전되거나 의미가 과도하게 일반화된 경우 contradicted로 처리합니다.
3. 주어진 원문 외의 일반 지식을 사용하지 마십시오.
"""

class SemanticCritic:
    def __init__(self, llm=None):
        # Allow injecting an LLM (e.g. mock for testing)
        self.llm = llm or ChatOpenAI(model="gpt-4o", temperature=0.0, max_retries=1, request_timeout=15.0)
        self.prompt = ChatPromptTemplate.from_messages([
            ("system", _CRITIC_SYSTEM_PROMPT),
            ("human", "원문(Evidence):\n{evidence}\n\n주장(Claim):\n{claim}"),
        ])
        
    def critique_claim(self, claim: Claim, lineage: SourceLineage) -> SemanticCriticResult:
        # If there's no evidence and it's factual, we should fail it
        if claim.claim_type in ("factual", "numerical", "attributed_statement") and not claim.evidence_ids:
            return SemanticCriticResult(
                claim_id=claim.claim_id,
                verdict="insufficient_evidence",
                evidence_ids=[],
                reason="No evidence provided for factual claim.",
                confidence=1.0
            )
            
        evidence_map = {ev.evidence_id: ev for ev in lineage.evidence_passages}
        evidence_text = "\n".join([evidence_map[ev_id].text for ev_id in claim.evidence_ids if ev_id in evidence_map])
        
        try:
            chain = self.prompt | self.llm
            response = chain.invoke({"evidence": evidence_text, "claim": claim.claim_text})
            
            # OpenAI sometimes wraps json in markdown
            content = response.content.strip()
            if content.startswith("```json"):
                content = content[7:-3].strip()
            
            data = json.loads(content)
            
            # Use Pydantic to validate
            try:
                # Use data from LLM if provided, otherwise fallback to known claim_id/evidence_ids
                # We expect the LLM to output claim_id and evidence_ids per the test, or we inject them.
                # Actually, our prompt doesn't ask for claim_id and evidence_ids. The tests inject them in the mock.
                res = SemanticCriticResult(
                    claim_id=data.get("claim_id", claim.claim_id),
                    verdict=data.get("verdict", ""),
                    evidence_ids=data.get("evidence_ids", claim.evidence_ids),
                    reason=data.get("reason", ""),
                    confidence=float(data.get("confidence", -1.0) if data.get("confidence") is not None else -1.0)
                )
            except Exception as e:
                raise QualityGateError("CRITIC_PARSE_ERROR", f"Validation failed: {str(e)}", claim.claim_id)
                
            if res.claim_id != claim.claim_id or res.evidence_ids != claim.evidence_ids:
                raise QualityGateError("CRITIC_RESPONSE_MISMATCH", "Critic response mapped to wrong claim/evidence", claim.claim_id)
                
            return res
        except QualityGateError:
            raise
        except Exception as e:
            # Any parser error, timeout, or schema error causes fail-closed
            raise QualityGateError("CRITIC_PARSE_ERROR", f"Critic failed to validate claim {claim.claim_id}: {str(e)}", claim.claim_id)


def run_semantic_critic(claims: List[Claim], lineage: SourceLineage, llm=None) -> None:
    """
    Run the semantic critic on all verified claims.
    If a claim is not supported, raises QualityGateError.
    """
    critic = SemanticCritic(llm=llm)
    for claim in claims:
        if claim.verification_status != "verified":
            raise QualityGateError("UNVERIFIED_CLAIM_PASSED_TO_CRITIC", "Deterministic verifier failed or skipped, cannot run critic.", claim.claim_id)
            
        res = critic.critique_claim(claim, lineage)
        if res.verdict != "supported":
            code = "CLAIM_CONTRADICTED" if res.verdict == "contradicted" else "CLAIM_INSUFFICIENT_EVIDENCE"
            raise QualityGateError(code, f"Critic verdict '{res.verdict}': {res.reason}", claim.claim_id)
