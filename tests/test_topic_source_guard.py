from src.qa.topic_source_guard import topic_matches_source


def test_glued_hangul_ascii_entity_rejects_unrelated_source():
    # "오픈AI"처럼 한글 뒤에 영문이 공백 없이 붙는 표기는 예전 토크나이저가
    # "오픈"+"AI"로 쪼개 강한 앵커가 하나도 안 남았고, 그 결과 가드가
    # 완전히 무관한 기사도 그냥 통과시켰다(재현된 버그).
    assert topic_matches_source(
        "오픈AI 신제품 공개",
        "무관한 제목",
        "완전히 무관한 기사입니다. 삼성 갤럭시 신제품 소식을 전합니다.",
    ) is False


def test_glued_hangul_ascii_entity_accepts_matching_source():
    assert topic_matches_source(
        "오픈AI 신제품 공개",
        "무관한 제목",
        "OpenAI가 새로운 모델을 공개했다고 밝혔다.",
    ) is True


def test_existing_spaced_alias_still_matches():
    # 기존에 이미 잘 되던(공백으로 분리된) 케이스가 회귀되지 않았는지 확인.
    assert topic_matches_source(
        "애플 신제품 공개",
        "무관한 제목",
        "Apple announced a new product today.",
    ) is True


def test_pure_year_and_ascii_tokens_still_tokenize_separately():
    from src.qa.topic_source_guard import _TOKEN_RE

    assert _TOKEN_RE.findall("애플 WWDC 2026 요약") == ["애플", "WWDC", "2026", "요약"]


def test_it_alone_is_not_a_strong_anchor():
    # 이 계정은 AI·IT·비즈니스 전문이라 후보 기사 대부분에 "IT"가 들어있다.
    # "ai"는 이미 일반 단어로 걸러지는데 "it"은 걸러지지 않아, 완전히 다른
    # 사건("애플 프로젝트 타이탄")을 "IT" 총정리 기사와 짝지어도 "IT"라는
    # 공통 단어 하나만으로 가드를 통과시키던 실제 버그(재현). "애플"이라는
    # 진짜 강한 앵커는 원문에 없으므로 "IT"를 걸러내면 여전히 불일치여야 한다.
    assert topic_matches_source(
        "IT 애플 프로젝트 타이탄의 여정",
        "2025년 IT 기술 동향 분석 및 핵심 트렌드 예측",
        "여러 기업의 다양한 IT 소식을 한데 모아 정리했다.",
    ) is False


def test_rejects_unrelated_korean_only_topic_with_no_ascii_anchors():
    """A pure-Korean topic with zero ASCII/alias anchors must not blindly pass.

    Both anchor-less previously meant ``topic_matches_source`` returned True
    unconditionally (see the ``if not strong_anchors: return True`` fallback),
    so a completely unrelated English source about a gaming PC deal was
    accepted as "matching" a topic about a New Jersey data center fine.
    """
    assert not topic_matches_source(
        "뉴저지 데이터 센터, 드론 사진으로 110만 달러 벌금 부과",
        "Sadly, this $1,549 RTX 5070-equipped gaming PC is a very good deal",
        "",
    )


def test_still_passes_korean_only_topic_with_no_numbers_or_ascii_anchors():
    """Existing permissive behavior must be preserved when there is truly
    nothing language-independent to check against (no numbers, no ASCII/alias
    anchors) -- see 748774b for why weak Korean descriptor matching is
    intentionally not enforced.
    """
    assert topic_matches_source(
        "국내 스타트업 투자 훈풍, 업계 반응은 엇갈려",
        "Startup funding sees a resurgence, but reactions are mixed",
        "",
    )


def test_accepts_korean_only_topic_when_amount_matches_source():
    assert topic_matches_source(
        "뉴저지 데이터 센터, 드론 사진으로 110만 달러 벌금 부과",
        "New Jersey fines data center operator $1.1 million over drone photos",
        "Regulators said the $1,100,000 penalty followed complaints about drone surveillance.",
    )
