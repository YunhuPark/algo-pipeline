from src.qa.topic_source_guard import topic_matches_source


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
