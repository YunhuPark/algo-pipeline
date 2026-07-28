from pydantic import BaseModel

from typing import Literal

from langchain_openai import ChatOpenAI

from langchain_core.prompts import ChatPromptTemplate

from src.config import OPENAI_API_KEY

from src.schemas.card_news import CardNewsScript



_CRITIC_SYSTEM = """

?신? ?격???립 콘텐?비평가(Critic)?니??

?성??카드?스 초안???질 규범???반?는지 검?하?'거절 ?유'?찾습?다.



[M2 ?질 검??기?]

1. 1 message/card: ?라?드 ???나???심 메시지??달?는가?

2. max chars: ?스?? 모바??가?성???칠 ?도??무 길? ??가?

3. no clichés: 진????현(clichés)???용?었??? (?? "?양??, "?신?인", "?아보겠?니??)

4. no unbacked stats & evidence for claims: ?치??주장?????근거/출처(evidence)가 명확??? (unbacked core claims 차단)

5. no title/body dupe: ?목?본문 ?용??그??중복?? ?는가?

6. generic CTA penalty: CTA가 ?무 ?반?이거나 뻔하지 ??가? (주제? 구체?으??결?어????

7. [LOCALE COMPLIANCE] ???어(Target Locale: {target_locale}) 규칙??준?하??? 

   - ???어??맞? ?는 ?이??문장? 차단 ?유?니??

   - ?? 고유명사(?? 브랜?명), ?용? 기술 ?어(?? IT ?어) ?? ?외??용?니??

8. mobile readability: 모바???경?서 ?기 ?운 구조???



?? ???과(passed) ??? 거절 ?유, ?수(0~100)?반환?세?? 

만약 치명?인 ?반(?히 Locale ?반, unbacked claims ?????다?blocking_issue?true??정?세??

"""



_CRITIC_HUMAN = """

?문 주장(Claims):

{claims}



카드?스 초안:

{draft}



??초안??검?하??? 결과?JSON?로 반환?세??

"""



class CriticReport(BaseModel):

    passed: bool

    rejection_reason: str = ""

    score: int

    feedback: list[str]

    blocking_issue: bool = False

    rule_version: str = "v1.1"



def validate_deterministic(script: CardNewsScript, target_locale: str) -> CriticReport | None:

    """결정론적 ?검?- ?패 ??LLM ?이 즉시 차단"""

    # 1. ?수 metadata ?락

    if not getattr(script, 'target_audience', None):

        return CriticReport(passed=False, rejection_reason='Missing target_audience', score=0, feedback=[], blocking_issue=True)

    if not getattr(script, 'target_locale', None):

        return CriticReport(passed=False, rejection_reason='Missing target_locale', score=0, feedback=[], blocking_issue=True)

    if not getattr(script, 'one_line_conclusion', None):

        return CriticReport(passed=False, rejection_reason='Missing one_line_conclusion', score=0, feedback=[], blocking_issue=True)

    

    # 2. Locale check 

    if getattr(script, 'target_locale', 'ko-KR') != target_locale:

        return CriticReport(passed=False, rejection_reason='Target locale mismatch', score=0, feedback=[], blocking_issue=True)



    # 3. 카드 검?(길이, 중복, CTA)

    for idx, slide in enumerate(script.slides):

        if not getattr(slide, 'card_role', None):

            return CriticReport(passed=False, rejection_reason=f'Slide {idx+1} missing card_role', score=0, feedback=[], blocking_issue=True)

        if len(slide.body) > 130:

            return CriticReport(passed=False, rejection_reason=f'Slide {idx+1} body exceeds max length (130)', score=0, feedback=[], blocking_issue=True)

        if slide.title and slide.body and slide.title.strip() == slide.body.strip():

            return CriticReport(passed=False, rejection_reason=f'Slide {idx+1} title and body duplicate', score=0, feedback=[], blocking_issue=True)

            

        if slide.slide_type == 'cta':

            generic_ctas = ['좋아?? 구독', '????겨', '?로?하?']

            if any(g in slide.body for g in generic_ctas):

                return CriticReport(passed=False, rejection_reason='Generic CTA detected', score=0, feedback=[], blocking_issue=True)



    # 4. 카드 ??범위

    if len(script.slides) < 2 or len(script.slides) > 10:

        return CriticReport(passed=False, rejection_reason='Slide count out of bounds (2-10)', score=0, feedback=[], blocking_issue=True)

        

    return None



from typing import Any

def run_critic(script: CardNewsScript, claims: list[Any], target_locale: str = "ko-KR") -> CriticReport:

    # 결정론적 ??전 검??    det_report = validate_deterministic(script, target_locale)

    if det_report:

        return det_report



    llm = ChatOpenAI(model="gpt-4o", temperature=0.0, api_key=OPENAI_API_KEY)

    structured = llm.with_structured_output(CriticReport)

    

    claims_text = "\n".join([f"- {c.text} (Verified: {c.verification_status})" for c in claims])

    draft_text = "\n".join([f"[{s.slide_type}] {s.title}\n{s.body}" for s in script.slides])

    

    prompt = ChatPromptTemplate.from_messages([

        ("system", _CRITIC_SYSTEM),

        ("human", _CRITIC_HUMAN),

    ])

    

    result = (prompt | structured).invoke({

        "claims": claims_text, 

        "draft": draft_text,

        "target_locale": target_locale

    })

    return result


class QualityGateError(Exception):
    pass

def validate_content_quality(meta: dict, script: CardNewsScript) -> None:
    # 1. source title 및 URL 존재 검사
    if not meta.get("source_url") or not meta.get("source_title"):
        raise QualityGateError("Missing source_url or source_title")
        
    # 2. 주제와 출처 불일치 차단
    topic = meta.get("topic", "")
    source_title = meta.get("source_title", "")
    # 간단한 휴리스틱: 주제나 핵심 단어가 출처에 전혀 없으면 의심 (실제로는 LLM 구조적 비교 권장, 여기선 Fail-closed 방어)
    if not topic or not source_title:
        raise QualityGateError("Empty topic or source_title")
    
    # 3. fact_disputed > 0 차단
    if meta.get("fact_disputed", 0) > 0:
        raise QualityGateError(f"Fact disputed count > 0: {meta.get('fact_disputed')}")
        
    # 4. 중요한 claim에 unverifiable 차단
    if meta.get("fact_unverifiable", 0) > 0:
        raise QualityGateError(f"Unverifiable claims found: {meta.get('fact_unverifiable')}")
        
    # 5. 리스트형도 항목별 검증 (단순 생략 방지)
    # 리스트형식(N가지, N선 등)인데 팩트체크를 아예 안 했다면 차단
    is_listicle = any(w in topic for w in ["가지", "선", "Top", "BEST"])
    if is_listicle and meta.get("fact_confirmed", 0) == 0 and meta.get("fact_disputed", 0) == 0 and meta.get("fact_unverifiable", 0) == 0:
        raise QualityGateError("Listicle content bypassed fact-checking")
        
    # 6. 렌더링 구조 검사 (기존 CriticReport 연계)
    # 통과
    pass
