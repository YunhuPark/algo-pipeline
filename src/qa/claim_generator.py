import json
import uuid
from typing import List, Dict, Any
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from src.schemas.card_news import Claim, SourceLineage, Slide, CardNewsScript

_CLAIM_SYSTEM_PROMPT = """
당신은 사실 관계를 엄밀하게 분리하는 분석기입니다.
주어진 원문(Evidence)을 바탕으로, 카드뉴스로 만들 핵심 사실들을 Claim(주장) 단위로 생성하세요.

반드시 원문에 있는 내용만 사용해야 하며, 다음 스키마의 JSON 리스트를 반환하세요.
{{
  "claims": [
    {{
      "claim_id": "c1",
      "claim_text": "원문에서 추출한 구체적 주장 문장",
      "claim_type": "factual" | "numerical" | "attributed_statement" | "inference" | "opinion" | "cta",
      "entities": ["언급된 고유명사", "회사명", "인명"],
      "numbers": [{{"raw_text": "3개", "normalized_value": 3.0, "unit": "개", "qualifier": "", "subject": ""}}],
      "dates": [{{"raw_text": "2026년 7월", "normalized_date": "2026-07", "precision": "month", "is_relative": false, "reference_date": ""}}],
      "evidence_ids": ["이 주장을 뒷받침하는 원문 단락의 ID"]
    }}
  ]
}}

규칙:
1. "3가지", "5가지"처럼 임의로 개수를 정하여 숫자를 만들어내지 마십시오.
2. 외부 일반 지식을 결합하지 마십시오.
3. 숫자가 포함된 문장은 반드시 numerical type을 사용하고, numbers 배열에 해당 숫자를 명시하십시오.
4. CTA 타입은 반드시 마지막에 하나만 넣고, 원문과 관련이 없는 "지금 당장 써보세요", "위험합니다" 식의 과도한 선동을 피하십시오.
"""

class ClaimGenerator:
    def __init__(self, llm=None):
        self.llm = llm or ChatOpenAI(
            model="gpt-4o", 
            temperature=0.2, 
            max_retries=1,
            model_kwargs={"response_format": {"type": "json_object"}}
        )
        self.prompt = ChatPromptTemplate.from_messages([
            ("system", _CLAIM_SYSTEM_PROMPT),
            ("human", "원문:\n{evidence}"),
        ])
        
    def generate_claims(self, lineage: SourceLineage) -> List[Claim]:
        if not lineage.evidence_passages:
            return []
            
        evidence_text = ""
        for ev in lineage.evidence_passages:
            evidence_text += f"[ID: {ev.evidence_id}]\n{ev.text}\n\n"
            
        chain = self.prompt | self.llm
        response = chain.invoke({"evidence": evidence_text})
        
        content = response.content.strip()
        
        # Strip markdown formatting robustly
        if content.startswith("```"):
            lines = content.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            content = "\n".join(lines).strip()
            
        try:
            data = json.loads(content)
        except json.JSONDecodeError as e:
            raise ValueError(f"Failed to parse claim JSON: {e}")
            
        if not isinstance(data, dict):
            raise ValueError("Expected JSON root to be an object")
            
        claims = []
        for c in data.get("claims", []):
            claim = Claim(
                claim_id=c.get("claim_id", str(uuid.uuid4())),
                claim_text=c.get("claim_text", ""),
                claim_type=c.get("claim_type", "factual"),
                entities=c.get("entities", []),
                numbers=c.get("numbers", []),
                dates=c.get("dates", []),
                evidence_ids=c.get("evidence_ids", []),
                source_url=lineage.source_url
            )
            claims.append(claim)
        return claims
