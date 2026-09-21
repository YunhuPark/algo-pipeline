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
