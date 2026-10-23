from src.qa.text_style import strip_sentence_periods as strip


def test_sentence_final_periods_are_removed():
    assert strip("오픈AI가 광고를 확대했다.") == "오픈AI가 광고를 확대했다"
    assert strip("8월에는 10억 달러를 찍었어.  \n다음 줄이야.") == "8월에는 10억 달러를 찍었어  \n다음 줄이야"
    assert strip("성장률이 12%.") == "성장률이 12%"
    assert strip("그는 \"가겠다.\" 라고 했다.") == "그는 \"가겠다\" 라고 했다"


def test_sentences_that_shared_a_line_move_to_their_own_lines():
    assert strip("첫 문장이다. 둘째 문장이다. 셋째다.") == "첫 문장이다\n둘째 문장이다\n셋째다"
    assert strip("확대되었습니다. 중남미 14개국을 포함해 진행 중입니다.") == (
        "확대되었습니다\n중남미 14개국을 포함해 진행 중입니다"
    )


def test_numbers_dates_domains_and_ellipses_are_kept():
    assert strip("3.5배 늘었다") == "3.5배 늘었다"
    assert strip("출처: AI타임스 · 2026.10.22") == "출처: AI타임스 · 2026.10.22"
    assert strip("원문: aitimes.com/news/articleView.html") == "원문: aitimes.com/news/articleView.html"
    assert strip("「26개국 추가... 전 세계 89개국으로 확대」") == "「26개국 추가... 전 세계 89개국으로 확대」"
    assert strip("") == ""
