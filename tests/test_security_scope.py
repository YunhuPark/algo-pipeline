"""AI-security articles are AI news; ordinary breaches are not - decided from what the article says.

The titles are real ones seen in the Korean security feeds (2026-10-28/29). Judging them by headline alone
once labelled an AI-security story "not AI"; these tests pin the decision to the AI terms in the text.
"""
from datetime import datetime

import pytest

from src.agents import news_collector as nc
from src.qa import security_scope as ss
from src.qa.hedge_guard import find_dropped_hedge

AI_SECURITY_TITLES = [
    "AI 활용 신종 랜섬웨어 조직 ‘n0n’, 14개국 23개 조직 공격 정황…몸값 요구액 4천억원 육박",
    "구글 크롬 보안 취약점 52건 긴급 수정… 앤트로픽 클로드 활용해 심각한 취약점 발견",
    "AI 해커, 3시간 49분 만에 산업제어 소프트웨어 치명적 취약점 4개 발견",
    "오픈소스 AI 실행도구 'llama.cpp'서 고위험 취약점 발견돼...악성 AI 모델 파일로 시스템 공격 가능",
    "미스트랄 AI 코딩도구 ‘바이브’서 고위험 취약점 발견…악성 저장소 통해 개발자 PC 장악 위험",
]
PLAIN_SECURITY_TITLES = [
    "금융보안원 “북한 IP 탐지됐지만 북한 해킹 단정 못해”…최근 금융권 침해사고와 연관성 확인 안 돼",
    "가상자산 거래소 잇단 보안사고…프라뱅, 이용자 자산 1억원 해킹 피해",
    "미국 CISA, ‘Node2Shell’ 취약점 실제 공격 악용 확인…서버·가상화 관리시스템 비상",
]


@pytest.mark.parametrize("title", AI_SECURITY_TITLES)
def test_a_security_story_with_ai_in_it_is_ai_news(title):
    assert ss.classify(title) == ss.AI_SECURITY


@pytest.mark.parametrize("title", PLAIN_SECURITY_TITLES)
def test_an_ordinary_breach_is_not_ai_news(title):
    assert ss.classify(title) == ss.PLAIN_SECURITY


def test_a_story_that_is_not_about_security_is_left_to_the_other_rules():
    assert ss.classify("MS, 비용 효율 20% 높인 차세대 보안 모델 출시") == ss.NOT_SECURITY


def test_the_body_decides_when_the_headline_shows_no_ai():
    title = "랜섬웨어 조직, 14개국 23개 조직 공격"
    assert ss.classify(title) == ss.PLAIN_SECURITY                               # headline only
    body = "공격자는 생성형 AI로 피싱 메일을 만들고 AI 에이전트로 내부망을 탐색했다."
    assert ss.classify(title, body=body) == ss.AI_SECURITY
    stray = "관련 기사: AI 시대의 보안 전략. 이번 사고는 지갑 서버에서 발생했다."
    assert ss.classify(title, body=stray) == ss.PLAIN_SECURITY                    # one stray mention is not enough


def test_ai_must_stand_alone_in_a_word():
    assert not ss.mentions_ai("MAIN server RAIL gateway")
    assert ss.mentions_ai("an AI model")


# ── the collector applies the same rule to the pool ────────────────────────────────────

def _item(title, summary=""):
    return nc.NewsItem(title=title, summary=summary, source="데일리시큐", url="https://x.test/a", published=datetime(2026, 10, 29))


@pytest.mark.parametrize("title", AI_SECURITY_TITLES)
def test_ai_security_articles_enter_the_ai_pool(title):
    assert nc._topic_tier(_item(title)) == 1


@pytest.mark.parametrize("title", PLAIN_SECURITY_TITLES)
def test_plain_breaches_stay_out_of_the_pool(title):
    assert nc._topic_tier(_item(title)) == 3


def test_the_korean_security_outlet_is_collected():
    assert any(name == "데일리시큐" and "dailysecu.com" in url for name, url in nc.RSS_FEEDS)


# ── hedges in the source must survive in the card ─────────────────────────────────────

EVIDENCE = (
    "금융보안원은 10월 21일 북한 IP 대역에서 약 440만 건의 스캔이 탐지됐다고 밝혔다. "
    "다만 IP는 위조될 수 있어 북한의 해킹으로 단정하기 어렵고 최근 금융권 침해사고와의 연관성은 확인되지 않았다."
)


def test_a_claim_that_states_a_hedged_attack_as_fact_is_refused():
    assert find_dropped_hedge("북한이 최근 금융권을 해킹했다", EVIDENCE) is not None


def test_the_same_claim_with_its_qualifier_passes():
    assert find_dropped_hedge("북한 해킹으로 단정하기는 어렵다고 금융보안원이 밝혔다", EVIDENCE) is None
    assert find_dropped_hedge("최근 금융권 침해사고와 북한의 연관성은 확인되지 않았다", EVIDENCE) is None


def test_a_factual_claim_from_the_same_sentence_is_not_flagged():
    # the scan count is stated plainly in the source; only the attribution is hedged
    assert find_dropped_hedge("금융보안원은 북한 IP 대역에서 약 440만 건의 스캔이 탐지됐다고 밝혔다", EVIDENCE) is None


def test_an_unrelated_claim_is_not_flagged():
    assert find_dropped_hedge("크롬 업데이트로 취약점 52건이 수정됐다", EVIDENCE) is None


def test_the_hedge_error_is_a_retryable_quality_error():
    from src.agents.content_creator import _FACTUAL_CLAIM_QUALITY_ERRORS

    assert "CLAIM_HEDGE_DROPPED" in _FACTUAL_CLAIM_QUALITY_ERRORS


def test_a_plain_breach_is_not_ai_adjacent_just_because_it_mentions_cloud_or_chips():
    # these used to enter the pool as "AI adjacent" through generic words in the summary
    assert nc._topic_tier(_item("일본 GMO인터넷, 고객 서버 해킹에 100만 달러 현상금", "클라우드 데이터센터의 고객 서버가 해킹됐다")) == 3
    assert nc._topic_tier(_item("중국 해커조직, 반도체 산업정보를 노린다", "반도체 업체 서버에 침투")) == 3
    # a non-security chip story is still AI adjacent
    assert nc._topic_tier(_item("엔비디아 대신 쓸 수 있는 신규 반도체 공개", "")) in (1, 2)
    assert nc._topic_tier(_item("국내 로봇 자동화 시장 성장", "")) == 2


# ── and it is enforced inside the real verifier, with a message the rewriter can act on ───

def _lineage(text):
    from src.schemas.card_news import EvidencePassage, SourceLineage

    return SourceLineage(
        schema_version="2.0", topic="테스트", source_title="테스트 기사", source_url="https://example.com/a",
        context="테스트", article_id="art_1", content_hash="hash",
        evidence_passages=[EvidencePassage(
            evidence_id="ev_1", article_id="art_1", source_url="https://example.com/a", text=text, content_hash="hash",
        )],
    )


def _claim(text):
    from src.schemas.card_news import Claim

    return Claim(claim_id="c1", claim_text=text, claim_type="factual", entities=["금융보안원"], evidence_ids=["ev_1"])


def test_the_verifier_refuses_a_dropped_hedge_and_accepts_the_qualified_claim():
    from src.qa.deterministic_verifier import DeterministicVerifier, QualityGateError

    lineage = _lineage(EVIDENCE)
    with pytest.raises(QualityGateError) as raised:
        DeterministicVerifier.verify_claims([_claim("금융보안원은 북한이 최근 금융권을 해킹했다고 밝혔다")], lineage)
    assert raised.value.error_code == "CLAIM_HEDGE_DROPPED"
    assert "qualifier" in str(raised.value)                       # tells the rewriter what to keep

    ok = _claim("금융보안원은 북한의 해킹으로 단정하기 어렵다고 밝혔다")
    DeterministicVerifier.verify_claims([ok], lineage)
    assert ok.verification_status == "verified"
