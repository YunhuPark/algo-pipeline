"""Regression tests for a round of correctness bugs found in a full-codebase
review of the numeric matching added/extended this session (compound Korean
numbers, ratio phrases, the verbatim-copy fallback). Each bug was reproduced
against the live code before being fixed; these lock the fix in.
"""
from types import SimpleNamespace
from decimal import Decimal

from src.qa.deterministic_verifier import (
    _number_supported_by_evidence,
    _extract_number_mentions,
    _extract_numeric_ranges,
    _extract_ratio_mentions,
)


def test_compound_number_does_not_fuse_an_unrelated_trailing_number():
    # "3억 2020년"은 "3억"(금액)과 "2020년"(연도) 두 개의 별개 사실이다.
    # 예전에는 잔여 숫자 캡처가 뒤따르는 아무 숫자나 삼켜 300,002,020이라는
    # 뜻없는 값 하나로 합쳐버렸다.
    assert list(_extract_number_mentions("이 아파트는 3억 2020년 준공되었다")) == [
        (Decimal("300000000"), ""),
        (Decimal("2020"), "year"),
    ]


def test_compound_number_with_genuine_residual_still_combines():
    # 진짜 같은 수량의 나머지 자리(뒤에 날짜/시간류 단위가 안 옴)는 여전히
    # 합쳐져야 한다 — 위 수정이 정상 케이스를 깨지 않았는지 확인.
    assert list(_extract_number_mentions("60만 3293명")) == [(Decimal("603293"), "people")]
    assert list(_extract_number_mentions("60만3293명")) == [(Decimal("603293"), "people")]


def test_verbatim_fallback_does_not_merge_unrelated_numbers_across_whitespace():
    # "20 26"(공백으로 구분된 두 개의 별개 숫자)이 공백만 제거하면 우연히
    # "2026"이 되어 무관한 연도와 겹쳐 오탐하던 문제.
    num_obj = SimpleNamespace(raw_text="20 26", unit="", normalized_value=Decimal("20"))
    assert _number_supported_by_evidence(num_obj, "이 회사는 2026년에 창립되었다.") is False


def test_verbatim_fallback_still_collapses_scale_word_spacing():
    # 스케일 단어 앞뒤 공백 차이("1만 4900원" vs "1만4900원")는 여전히
    # 같은 숫자로 인정돼야 한다.
    num_obj = SimpleNamespace(raw_text="1만 4900원", unit="krw", normalized_value=Decimal("14900"))
    assert _number_supported_by_evidence(
        num_obj, "1만4900원짜리 간장게장 백반을 구매했다."
    ) is True


def test_range_with_compound_endpoint_is_not_silently_corrupted():
    # 끝점이 복합수("60만 3293명")인 범위를 예전 코드는 "3293명"만 끝점으로
    # 잘못 인식하고 다른 쪽 끝점의 스케일(만)을 물려받아 32,930,000처럼
    # 실제 값의 54배 가까이 부풀렸다. 인식 못 하면 조용히 틀린 값을 내는
    # 대신 아예 인식하지 않아야 한다.
    assert list(_extract_numeric_ranges("가격은 60만 3293명에서 70만명 사이다")) == []


def test_range_without_compound_endpoints_still_works():
    ranges = list(
        _extract_numeric_ranges("The valuation was $7.2 billion to $7.45 billion.")
    )
    assert len(ranges) == 1
    (left_value, left_unit), (right_value, right_unit) = ranges[0]
    assert left_value == Decimal("7200000000.0")
    assert right_value == Decimal("7450000000.00")
    assert left_unit == right_unit == "usd"


def test_ratio_with_compound_denominator_is_not_silently_truncated():
    # "60만 3293명 중 1만명"의 분모(60만3293)를 예전 코드는 "3293"만 잡아
    # 완전히 틀린 분모로 검증하려 했다.
    assert list(_extract_ratio_mentions("60만 3293명 중 1만명이 응답했다")) == []


def test_ratio_without_compound_parts_still_works():
    assert list(_extract_ratio_mentions("10명 중 6명")) == [
        (Decimal("10"), Decimal("6"), "people")
    ]
