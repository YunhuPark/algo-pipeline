from __future__ import annotations

import inspect
import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image, ImageChops

import src.agents.design_renderer as renderer
from src import pipeline, source_note as sn
from src.agents import publisher
from src.qa import script_assembler
from src.schemas.card_news import CardNewsScript, Slide

TECHCRUNCH = (
    "https://techcrunch.com/2026/10/07/nous-research-confirms-it-hit-1-5b-valuation/"
    "?utm_source=rss&utm_medium=feed"
)


# ── outlet and title ──────────────────────────────────────


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://techcrunch.com/2026/10/07/a", "TechCrunch"),
        ("https://www.theverge.com/gadgets/1", "The Verge"),
        ("https://news.samsungsds.com/kr/insights/x.html", "삼성SDS"),
        ("https://www.bbc.co.uk/news/1", "BBC"),
        ("https://zdnet.co.kr/view/?no=1", "ZDNet Korea"),
        ("https://blog.google/products/x", "Google"),
        ("https://some-new-site.com/a", "Some-new-site"),
    ],
)
def test_outlet_is_named_from_the_domain(url, expected):
    assert sn.outlet_from_url(url) == expected


def test_garbage_urls_have_no_outlet():
    assert sn.outlet_from_url("") == ""
    assert sn.outlet_from_url("not a url") == ""


def test_clean_title_drops_the_trailing_site_name():
    assert (
        sn.clean_title("2025년 IT 시장을 주도하고 있는 주요 트렌드 | 인사이트리포트 | 삼성SDS")
        == "2025년 IT 시장을 주도하고 있는 주요 트렌드"
    )
    assert sn.clean_title("OpenAI ships a model - The Verge") == "OpenAI ships a model"


def test_a_short_first_segment_is_not_mistaken_for_the_headline():
    assert sn.clean_title("News | A much longer and real headline here") == (
        "A much longer and real headline here"
    )


def test_long_titles_are_cut_at_a_word_boundary_never_mid_word():
    title = (
        "Nous Research confirms it hit $1.5B valuation, launches AI agents "
        "for business users and more"
    )

    cut = sn.clean_title(title)

    assert cut.endswith("…")
    assert len(cut) <= 90
    body = cut[:-1]
    assert title.startswith(body)
    # The next character in the original is a space: the cut did not split a word.
    assert title[len(body)] == " "


def test_blank_title_stays_blank():
    assert sn.clean_title("   ") == ""


# ── the note ──────────────────────────────────────────────


def _note(**kw) -> sn.SourceNote:
    base = dict(
        outlet="TechCrunch",
        title="Nous Research hits $1.5B",
        url=TECHCRUNCH,
        published=date(2026, 10, 7),
    )
    base.update(kw)
    return sn.SourceNote(**base)


def test_card_lines_and_caption_block_carry_outlet_title_date_and_link():
    note = _note()

    assert note.date_text() == "2026.10.07"
    assert note.card_lines() == ["TechCrunch", "Nous Research hits $1.5B", "2026.10.07 발행"]
    assert note.caption_block().splitlines() == [
        "출처: TechCrunch · 2026.10.07",
        "「Nous Research hits $1.5B」",
        "원문: techcrunch.com/2026/10/07/nous-research-confirms-it-hit-1-5b-valuation",
    ]


def test_tracking_parameters_never_reach_the_caption():
    assert "utm_" not in _note().caption_block()


def test_an_unknown_date_is_left_out_not_guessed():
    note = _note(published=None)

    assert note.date_text() == ""
    assert "발행" not in " ".join(note.card_lines())
    assert note.caption_block().splitlines()[0] == "출처: TechCrunch"


def test_a_link_too_long_to_show_whole_is_omitted_rather_than_cut():
    note = _note(url="https://techcrunch.com/" + "a" * 200)

    assert "원문" not in note.caption_block()


def test_round_trip_through_script_json():
    note = _note()

    assert sn.SourceNote.from_dict(json.loads(json.dumps(note.to_dict()))) == note
    assert sn.SourceNote.from_dict(None) is None
    assert sn.SourceNote.from_dict({}) is None
    assert sn.SourceNote.from_dict({"outlet": "X", "published": "garbage"}).published is None


# ── building it from a lineage ────────────────────────────


def _lineage(**kw):
    base = dict(
        source_url=TECHCRUNCH,
        source_title="Nous Research hits $1.5B | TechCrunch",
        published_at="2026-10-07T09:00:00+00:00",
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_note_uses_the_recorded_publication_date(monkeypatch):
    monkeypatch.setattr(
        "src.qa.source_freshness.fetch_published_date",
        lambda url: pytest.fail("must not fetch when the date is already known"),
    )

    note = sn.from_lineage(_lineage())

    assert (note.outlet, note.title, note.published) == (
        "TechCrunch",
        "Nous Research hits $1.5B",
        date(2026, 10, 7),
    )


def test_a_missing_date_is_read_from_the_page_once(monkeypatch):
    monkeypatch.setattr(
        "src.qa.source_freshness.fetch_published_date", lambda url: date(2026, 10, 6)
    )

    assert sn.from_lineage(_lineage(published_at="")).published == date(2026, 10, 6)


def test_a_date_that_cannot_be_found_stays_blank(monkeypatch):
    monkeypatch.setattr("src.qa.source_freshness.fetch_published_date", lambda url: None)

    assert sn.from_lineage(_lineage(published_at="")).published is None
    assert sn.from_lineage(_lineage(published_at=""), fetch_missing_date=False).published is None


def test_nothing_to_attribute_means_no_note():
    assert sn.from_lineage(None) is None
    assert sn.from_lineage(_lineage(source_url="", source_title="")) is None


# ── caption placement ─────────────────────────────────────


def test_attribution_goes_before_the_trailing_hashtags():
    caption = "첫 줄\n\n· 하나\n\n@algo__kr\n#알고 #카드뉴스"

    out = sn.insert_into_caption(caption, "출처: TechCrunch", "#알고 #카드뉴스")

    assert out.endswith("출처: TechCrunch\n\n#알고 #카드뉴스")
    assert out.index("@algo__kr") < out.index("출처")


def test_attribution_is_appended_when_there_are_no_trailing_hashtags():
    assert sn.insert_into_caption("본문", "출처: X", "#a").endswith("본문\n\n출처: X")
    assert sn.insert_into_caption("본문", "", "#a") == "본문"


# ── the credit line on each content card ──────────────────


def _fonts(monkeypatch):
    for regular, bold in (
        ("C:/Windows/Fonts/malgun.ttf", "C:/Windows/Fonts/malgunbd.ttf"),
        ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"),
        (
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ),
    ):
        if Path(regular).exists() and Path(bold).exists():
            monkeypatch.setattr(
                renderer, "_find_font", lambda kind="bold": bold if kind == "bold" else regular
            )
            return
    pytest.skip("No portable test font pair is installed")


def _cta() -> Slide:
    return Slide(
        slide_number=6,
        slide_type="cta",
        title="여러분의 생각은 어떠신가요",
        body="이 흐름을 어떻게 보시나요? 저장하고 의견을 남겨주세요.",
    )


def _blank() -> Image.Image:
    return Image.new("RGB", (renderer.W, renderer.H), (11, 13, 29))


def _changed_box(a: Image.Image, b: Image.Image):
    return ImageChops.difference(a, b).getbbox()


def test_the_credit_is_one_small_line_along_the_bottom_edge(monkeypatch):
    _fonts(monkeypatch)

    plain = _blank()
    credited = renderer._draw_source_footer(_blank(), _note())

    left, top, right, bottom = _changed_box(plain, credited)
    assert top >= renderer.H - 60          # only the bottom strip changed
    assert bottom <= renderer.H - 10       # and clear of the accent line at the very edge
    assert right <= renderer.W - renderer.PAD
    assert bottom - top <= 30              # one small line, not a box


def test_the_video_channel_is_named_when_the_card_has_a_video(monkeypatch):
    _fonts(monkeypatch)

    article_only = renderer._draw_source_footer(_blank(), _note())
    with_video = renderer._draw_source_footer(_blank(), _note(), "Hyperautomation Labs")
    unknown_channel = renderer._draw_source_footer(_blank(), _note(), "")

    assert _changed_box(article_only, with_video) is not None       # channel adds text
    assert _changed_box(with_video, unknown_channel) is not None    # "" falls back to "YouTube"
    assert _changed_box(article_only, unknown_channel) is not None


def test_a_card_with_neither_source_nor_video_gets_no_footer(monkeypatch):
    _fonts(monkeypatch)

    assert _changed_box(_blank(), renderer._draw_source_footer(_blank(), None, None)) is None


def test_a_very_long_channel_name_cannot_run_off_the_card(monkeypatch):
    _fonts(monkeypatch)

    credited = renderer._draw_source_footer(_blank(), _note(), "가나다라마바사" * 30)

    _, _, right, _ = _changed_box(_blank(), credited)
    assert right <= renderer.W - renderer.PAD + 2


def test_the_closing_card_no_longer_carries_a_source_box(monkeypatch):
    # Moved to a small line on each content card; the last card stays uncluttered.
    _fonts(monkeypatch)
    assert "source" not in inspect.signature(renderer._render_cta).parameters


def test_render_card_set_credits_every_content_card_and_only_those(monkeypatch, tmp_path):
    from src.agents.youtube_fetcher import VideoInfo

    _fonts(monkeypatch)
    monkeypatch.setattr(renderer, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(renderer, "_generate_caption", lambda script, handle: "캡션\n#알고")
    monkeypatch.setattr("src.agents.image_searcher.search_pexels", lambda *a, **k: None)
    calls: list[tuple] = []
    real = renderer._draw_source_footer
    monkeypatch.setattr(
        renderer,
        "_draw_source_footer",
        lambda img, source=None, video_creator=None: (
            calls.append((source, video_creator)) or real(img, source, video_creator)
        ),
    )
    script = CardNewsScript(
        topic="Nous 15억 달러",
        hook="hook",
        hashtags=["#알고"],
        slides=[
            Slide(slide_number=1, slide_type="cover", title="표지", body="요약"),
            Slide(slide_number=2, slide_type="content", title="영상 카드", body="검증된 본문입니다."),
            Slide(slide_number=3, slide_type="content", title="문장 카드", body="검증된 본문입니다."),
            _cta().model_copy(update={"slide_number": 4}),
        ],
    )
    video = VideoInfo(
        video_id="abc", url="youtu.be/abc", title="t", creator="Hyperautomation Labs",
        thumbnail=Image.new("RGB", (640, 360), (40, 40, 40)),
    )

    renderer.render_card_set(
        script, Image.new("RGB", (1080, 1350), (15, 16, 30)),
        output_subdir="credits", video_infos=[video, None], source=_note(),
    )

    # One call per content card: the video card names its channel, the text card does not.
    assert [creator for _, creator in calls] == ["Hyperautomation Labs", None]
    assert all(source == _note() for source, _ in calls)


def test_render_card_set_puts_the_source_in_caption_and_script_json(monkeypatch, tmp_path):
    _fonts(monkeypatch)
    monkeypatch.setattr(renderer, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(
        renderer, "_generate_caption", lambda script, handle: "본문 캡션\n\n@algo\n#알고 #카드뉴스"
    )
    monkeypatch.setattr("src.agents.image_searcher.search_pexels", lambda *a, **k: None)
    script = CardNewsScript(
        topic="Nous 15억 달러",
        hook="hook",
        hashtags=["#알고", "#카드뉴스"],
        slides=[
            Slide(slide_number=1, slide_type="cover", title="표지", body="요약"),
            Slide(slide_number=2, slide_type="content", title="근거", body="검증된 본문입니다."),
            _cta().model_copy(update={"slide_number": 3}),
        ],
    )

    renderer.render_card_set(
        script,
        Image.new("RGB", (1080, 1350), (15, 16, 30)),
        output_subdir="with-source",
        source=_note(),
    )

    out = tmp_path / "with-source"
    caption = (out / "caption.txt").read_text(encoding="utf-8")
    assert "출처: TechCrunch · 2026.10.07" in caption
    assert caption.rstrip().endswith("#알고 #카드뉴스")  # hashtags stay last
    saved = json.loads((out / "script.json").read_text(encoding="utf-8"))
    assert sn.SourceNote.from_dict(saved["source_note"]) == _note()


# ── what gets posted ──────────────────────────────────────


def test_the_reviewed_caption_is_what_gets_published(tmp_path):
    (tmp_path / "caption.txt").write_text("  검토한 캡션\n\n출처: X  \n", encoding="utf-8")

    assert publisher.read_card_caption(tmp_path) == "검토한 캡션\n\n출처: X"
    assert publisher._final_caption("검토한 캡션", "hook", ["#a"]) == "검토한 캡션"


def test_publishing_falls_back_to_hook_and_hashtags_without_a_reviewed_caption(tmp_path):
    assert publisher.read_card_caption(tmp_path) == ""
    assert publisher._final_caption("", "hook", ["#a", "#b"]) == publisher._build_caption(
        "hook", ["#a", "#b"]
    )
    assert publisher._final_caption(None, "hook", ["#a"]) == publisher._build_caption(
        "hook", ["#a"]
    )


def test_an_over_long_caption_is_trimmed_to_instagrams_limit():
    out = publisher._final_caption("가" * 5000, "hook", [])

    assert len(out) == publisher.INSTAGRAM_CAPTION_LIMIT
    assert out.endswith("…")


# ── wiring ────────────────────────────────────────────────


def test_fact_check_hashtag_is_no_longer_added():
    tags = script_assembler._hashtags("Nous Research 15억 달러", [])

    assert "#팩트체크" not in tags
    assert {"#알고", "#카드뉴스", "#뉴스분석", "#인사이트"} <= set(tags)


def test_the_pipeline_builds_the_note_from_the_lineage_and_passes_it_to_the_renderer():
    source = inspect.getsource(pipeline._run_once)

    assert "from_lineage(source_lineage)" in source
    assert "source=source_note" in source
    assert "read_card_caption" in source
    assert '"video_creator"' in source          # kept so a later edit can redraw the credit


def test_a_slide_edit_keeps_the_credit_line_and_the_caption_source():
    from src.dashboard import app as dashboard

    source = inspect.getsource(dashboard)

    assert 'SourceNote.from_dict(script_data.get("source_note"))' in source
    assert "_draw_source_footer" in source
    assert 'slide_data.get("video_creator")' in source
    assert "insert_into_caption" in source
