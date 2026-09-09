import json

from PIL import Image, ImageChops

import src.agents.design_renderer as renderer
from src.schemas.card_news import CardNewsScript, Slide


def _use_test_fonts(monkeypatch):
    regular = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    bold = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    monkeypatch.setattr(renderer, "_find_font", lambda kind="bold": bold if kind == "bold" else regular)


def _slide(visual_type: str, **updates) -> Slide:
    data = {
        "slide_number": 2,
        "slide_type": "content",
        "title": "Verified market signal",
        "body": "The source reports a verified change, explains its mechanism, and states the remaining limit.",
        "accent": "$48B",
        "visual_type": visual_type,
        "visual_values": ["$48B", "$20B"],
        "visual_labels": ["Valuation", "Funding"],
    }
    data.update(updates)
    return Slide(**data)


def test_evidence_driven_layouts_render_as_distinct_full_size_cards(monkeypatch):
    _use_test_fonts(monkeypatch)
    background = Image.new("RGB", (renderer.W, renderer.H), (11, 13, 29))

    hero = renderer._render_content(background, _slide("hero_stat"), 6, "@algo")
    comparison = renderer._render_content(background, _slide("comparison"), 6, "@algo")
    process = renderer._render_content(background, _slide("process"), 6, "@algo")

    assert hero.size == (1080, 1350)
    assert comparison.size == (1080, 1350)
    assert process.size == (1080, 1350)
    assert ImageChops.difference(hero, comparison).getbbox() is not None
    assert ImageChops.difference(comparison, process).getbbox() is not None


def test_process_layout_keeps_korean_conjugation_intact():
    body = "입력을 분석하고 검증 단계를 거쳐 결과를 만든다."

    parts = renderer._split_verified_clauses(body)

    reconstructed = "".join(parts).replace(" ", "")
    assert reconstructed == body.rstrip(".").replace(" ", "")
    assert "분석하고" in " ".join(parts)


def test_render_card_set_persists_visual_metadata(monkeypatch, tmp_path):
    _use_test_fonts(monkeypatch)
    monkeypatch.setattr(renderer, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(renderer, "_generate_caption", lambda *_args, **_kwargs: "caption")
    monkeypatch.setattr("src.agents.image_searcher.search_pexels", lambda *_args, **_kwargs: None)

    script = CardNewsScript(
        topic="Verified market signal",
        hook="Read the verified evidence",
        hashtags=["#verified", "#news"],
        slides=[
            Slide(slide_number=1, slide_type="cover", title="Market signal", body="Evidence summary"),
            _slide("hero_stat"),
            Slide(slide_number=3, slide_type="cta", title="Read the source", body="Review the original report."),
        ],
    )

    renderer.render_card_set(
        script,
        Image.new("RGB", (1080, 1350), (15, 16, 30)),
        output_subdir="visual-test",
    )

    payload = json.loads((tmp_path / "visual-test" / "script.json").read_text(encoding="utf-8"))
    saved = payload["slides"][1]
    assert saved["accent"] == "$48B"
    assert saved["visual_type"] == "hero_stat"
    assert saved["visual_values"] == ["$48B", "$20B"]
    assert saved["visual_labels"] == ["Valuation", "Funding"]
