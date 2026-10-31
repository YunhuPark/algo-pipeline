"""Rules that stop manual fixes: headline figure on the cover, figures in the caption, chip labels, CTA title.

The cases are the real sets reviewed on 2026-10-29/30 (#28 ads in 89 countries, #33 MS security model,
#35 OpenAI usage x2), so a regression brings back the exact problem that was fixed by hand.
"""
import json

import pytest

from src.qa import card_rules as cr
from src.qa.preview_audit import audit_output
from src.qa.script_assembler import ScriptAssembler, _cta_summary
from src.schemas.card_news import Claim, Slide

H35 = "오픈AI, 프로·기업용 AI 사용량 최대 2배 확대"
H33 = "MS, 비용 효율 20% 높인 차세대 보안 모델 'MAI 사이버 1.1 플래시' 출시"
H28 = "오픈AI, 챗GPT 광고 서비스 89개국으로 확대"


# ── 1. cover leads with the headline's figure ──────────────────────────────────────────

def test_a_cover_with_a_secondary_figure_is_not_accepted_when_the_headline_has_another():
    assert not cr.cover_has_headline_figure("200달러 요금제", H35)           # the plan price, not the news
    assert cr.cover_has_headline_figure("AI 사용량 최대 2배 확대", H35)
    assert cr.cover_has_headline_figure("89개국 광고 서비스", H28)
    assert cr.cover_has_headline_figure("무엇이든", "오픈AI가 새 모델 공개")     # no figure in the headline: no demand


def test_years_in_a_headline_are_not_headline_figures():
    assert cr.figure_digits("2026년 AI 전망 3배") == {"3"}
    assert cr.cover_has_headline_figure("AI 전망", "2026년 AI 전망")


def test_the_cover_phrase_is_cut_from_the_headline_around_its_figure():
    assert cr.headline_figure_phrase(H35) == "프로·기업용 AI 사용량 최대 2배 확대"
    assert cr.headline_figure_phrase(H28) == "챗GPT 광고 서비스 89개국으로 확대"
    long_phrase = cr.headline_figure_phrase(H33)
    assert long_phrase == "비용 효율 20% 높인 차세대 보안 모델"             # ends on a whole phrase, not on "높인"
    assert "20%" in long_phrase and len(long_phrase) <= 22
    assert cr.headline_figure_phrase("오픈AI가 새 모델 공개") == ""


def _claims(bodies):
    return [
        Claim(claim_id=f"c{i}", claim_text=body, display_title=title, editorial_role=role, claim_type="factual",
              evidence_ids=["e1"], verification_status="verified")
        for i, (body, title, role) in enumerate(
            zip(bodies, ["사용량 두 배 확대", "요금제 변경 철회", "기본 제공량 확대", "사용량 제한 완화"],
                ["change", "context", "evidence", "limitation"]), start=1)
    ]


BODIES = [
    "오픈AI는 프로 및 기업용 요금제의 AI 사용량을 최대 2배로 늘렸다.",
    "오픈AI는 월 200달러 요금제의 사용량 축소 계획을 철회했다.",
    "프로 100, 200, 500 요금제의 기본 제공량이 2배로 늘어났다.",
    "오픈AI는 사용량 제한을 완화해 더 많은 AI 작업을 수행할 수 있게 했다.",
]


def test_the_assembler_replaces_a_cover_that_leads_with_the_wrong_figure(monkeypatch):
    from src.qa import script_assembler as sa

    monkeypatch.setattr(sa, "_generate_cover_copy", lambda *a, **k: sa._CoverCopy(
        cover_title="200달러 요금제", hook="AI 사용이 더 자유로워집니다", cta_title="당신은 어떻게 생각하나요"))
    script = ScriptAssembler.assemble(H35, _claims(BODIES), num_cards=6)
    assert "2배" in script.cover.title and len(script.cover.title) <= 22
    assert script.cta.title == "핵심만 다시 보면"


# ── 4. CTA title ───────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("title", ["당신은 어떻게 생각하나요", "여러분의 의견은?", "이 모델의 특징은", ""])
def test_unanswerable_or_unfinished_cta_titles_are_replaced(title):
    assert cr.clean_cta_title(title) == "핵심만 다시 보면"


@pytest.mark.parametrize("title", ["어디까지 확장될까", "가장 눈에 띈 포인트는?", "저장해두세요"])
def test_ordinary_cta_titles_are_kept(title):
    assert cr.clean_cta_title(title) == title


# ── 3. chip labels ─────────────────────────────────────────────────────────────────────

def test_a_chip_label_names_the_figure_not_another_cards_title():
    assert cr.chip_label("중남미 14개국 추가", "14개국", ["광고 서비스"], []) == "중남미 추가"      # figure in the title
    assert cr.chip_label("사용량 두 배 확대", "2배", ["AI 사용량"], []) == "AI 사용량"             # the card's own label
    assert cr.chip_label("요금제 변경 철회", "200달러", ["요금제"], ["AI 사용량"]) == "요금제"


def test_generic_or_repeated_labels_fall_back_to_the_headline():
    assert cr.chip_label("광고주 10만곳 캠페인", "10만곳", ["핵심 수치"], []) == "광고주 캠페인"
    assert cr.chip_label("요금제 변경 철회", "200달러", ["핵심 수치"], []) == "요금제 변경 철회"
    assert cr.chip_label("요금제 변경 철회", "200달러", ["광고 서비스"], ["광고 서비스"]) == "요금제 변경 철회"


def test_closing_card_chips_for_set_35_are_labelled_correctly():
    slides = [
        Slide(slide_number=2, slide_type="content", title="사용량 두 배 확대", body="x" * 40,
              visual_values=["2배"], visual_labels=["AI 사용량"]),
        Slide(slide_number=3, slide_type="content", title="요금제 변경 철회", body="x" * 40,
              visual_values=["200달러"], visual_labels=["요금제"]),
    ]
    _, values, labels = _cta_summary(slides)
    assert values == ["2배", "200달러"] and labels == ["AI 사용량", "요금제"]


# ── 2. caption figures ─────────────────────────────────────────────────────────────────

def test_casual_endings():
    assert cr.casual("오픈AI는 사용량을 최대 2배로 늘렸습니다.") == "오픈AI는 사용량을 최대 2배로 늘렸어"
    assert cr.casual("월 200달러 요금제의 계획을 철회했다.") == "월 200달러 요금제의 계획을 철회했어"
    assert cr.casual("기본 제공량이 2배가 되었습니다") == "기본 제공량이 2배가 되었어"


SCRIPT35 = {
    "slides": [
        {"slide_type": "cover", "title": "AI 사용량 최대 2배 확대", "body": "x"},
        {"slide_type": "content", "title": "사용량 두 배 확대", "body": BODIES[0], "visual_values": ["2배"]},
        {"slide_type": "content", "title": "요금제 변경 철회", "body": BODIES[1], "visual_values": ["200달러"]},
        {"slide_type": "content", "title": "기본 제공량 확대", "body": BODIES[2], "visual_values": ["2배"]},
        {"slide_type": "content", "title": "사용량 제한 완화", "body": BODIES[3], "visual_values": ["오픈AI"]},
        {"slide_type": "cta", "title": "핵심만 다시 보면", "body": "y"},
    ]
}
CAPTION35 = (
    "⚡ AI 사용이 더 자유로워집니다\n\n· 사용량 두 배 확대\n· 요금제 변경 철회\n· 기본 제공량 확대\n\n"
    "저장해두고 나중에 다시 확인해요 💾\n\n@algo__kr\n\n출처: AI타임스 · 2026.10.30\n\n#알고 #카드뉴스"
)


def test_a_caption_without_the_cards_figures_is_rebuilt_from_the_cards_sentences():
    repaired, note = cr.repair_caption_figures(CAPTION35, SCRIPT35)
    assert "2배" in repaired and "200달러" in repaired
    assert repaired.splitlines()[0] == "⚡ AI 사용량 최대 2배 확대"                 # the opening carries the cover figure
    bullets = [line for line in repaired.splitlines() if line.startswith("· ")]
    assert 3 <= len(bullets) <= 4 and len(set(bullets)) == len(bullets)
    assert "출처: AI타임스" in repaired and "#알고" in repaired                      # the rest is untouched
    assert note


def test_a_caption_that_already_has_the_figures_is_left_alone():
    good = "⚡ 프로·기업용 AI 사용량 최대 2배 확대\n\n· 요금제 사용량을 최대 2배로 늘렸어  \n· 월 200달러 요금제 축소 계획을 철회했어  \n\n출처: x"
    repaired, note = cr.repair_caption_figures(good, SCRIPT35)
    assert repaired == good and note == ""


def test_the_audit_applies_the_caption_repair_and_reports_it(tmp_path):
    (tmp_path / "caption.txt").write_text(CAPTION35, encoding="utf-8")
    (tmp_path / "script.json").write_text(json.dumps(SCRIPT35, ensure_ascii=False), encoding="utf-8")
    result = audit_output(tmp_path, "")
    text = (tmp_path / "caption.txt").read_text(encoding="utf-8")
    assert "2배" in text and "200달러" in text
    assert any("수치" in item for item in result.get("fixed", []))
