"""Render a build-in-public "운영일지" episode (docs/devlog/epNN.json) with the same card renderer.

The news pipeline needs a source article; an episode's source is the project's own data (git, the
databases, the Instagram API), recorded in the episode file's "evidence" block. Nothing here calls
an AI model or posts anything: it writes a card folder that can be reviewed like any other set.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path

from PIL import Image

from src.qa.text_style import strip_sentence_periods


def _gradient_background(size: tuple[int, int] = (1080, 1350)) -> Image.Image:
    """A dark brand gradient (no stock photo, so nothing to credit or mismatch)."""
    import numpy as np

    width, height = size
    y = np.linspace(0, 1, height)[:, None, None]
    top, bottom = np.array([22, 30, 62]), np.array([6, 8, 20])
    column = (top * (1 - y) + bottom * y).astype("uint8")
    return Image.fromarray(np.repeat(column, width, axis=1))


def caption_for(episode: dict) -> str:
    bullets = "\n".join(f"· {b}" for b in episode["caption_bullets"])
    note = episode["source"]
    published = str(note.get("published", "")).replace("-", ".")
    parts = [
        episode["topic"],
        bullets,
        episode["caption_closing"],
        "@algo__kr",
        f"출처: {note['outlet']} · {published}",
        " ".join(episode["hashtags"]),
    ]
    return strip_sentence_periods("\n\n".join(parts))


def render_devlog(episode: dict, out_root: Path = Path("output")) -> Path:
    """Draw the episode's cards into a new folder under out_root and return it."""
    from src.agents import design_renderer as dr
    from src.schemas.card_news import CardNewsScript, Slide
    from src.source_note import SourceNote

    persona = dr.load_persona()
    dr.STYLE = dr._build_style(persona)
    handle = persona.handle

    cover, cta = episode["cover"], episode["cta"]
    slides = [Slide(slide_number=1, slide_type="cover", title=cover["title"], body=cover["hook"])]
    for item in episode["slides"]:
        slides.append(Slide(
            slide_number=len(slides) + 1, slide_type="content", title=item["title"], body=item["body"],
            visual_type=item.get("visual_type", "auto"),
            visual_values=item.get("visual_values", []), visual_labels=item.get("visual_labels", []),
        ))
    chips = cta.get("chips", [])
    slides.append(Slide(
        slide_number=len(slides) + 1, slide_type="cta", title=cta["title"],
        body=f"{cta['takeaway']}\n{cta['follow']}", emoji="👇",
        visual_values=[c[0] for c in chips], visual_labels=[c[1] for c in chips],
    ))
    script = CardNewsScript(topic=episode["topic"], hook=cover["hook"], slides=slides, hashtags=episode["hashtags"])

    note = episode["source"]
    source = SourceNote(outlet=note["outlet"], title="", url="", published=date.fromisoformat(note["published"]))

    base = _gradient_background()
    bg, bg_cover = dr._apply_background(base), dr._apply_background(base, for_cover=True)
    safe = re.sub(r"[^\w가-힣]", "_", episode["topic"])[:20]
    out_dir = Path(out_root) / f"{datetime.now():%Y%m%d_%H%M}_{safe}"
    out_dir.mkdir(parents=True, exist_ok=True)

    total, layout = len(slides), []
    for slide in slides:
        dr._PANEL_PROBE.clear()
        if slide.slide_type == "cover":
            rendered = dr._render_cover(bg_cover, slide, total, handle, hook=script.hook)
        elif slide.slide_type == "cta":
            rendered = dr._render_cta(bg, slide, total, handle, script.hashtags)
        else:
            rendered = dr._render_content(bg, slide, total, handle)
            rendered = dr._draw_source_footer(rendered, source, None)
        layout.append({
            "slide": slide.slide_number, "type": slide.slide_type, "video": False,
            "panel": dict(dr._PANEL_PROBE[-1]) if dr._PANEL_PROBE else None,
        })
        rendered.convert("RGB").save(out_dir / f"card_{slide.slide_number:02d}_{slide.slide_type}.png", "PNG", optimize=True)

    base.save(out_dir / "background.png", "PNG")
    (out_dir / "layout.json").write_text(json.dumps(layout, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "caption.txt").write_text(caption_for(episode), encoding="utf-8")
    (out_dir / "hashtags.txt").write_text(" ".join(episode["hashtags"]), encoding="utf-8")
    script_data = {
        "topic": script.topic, "hook": script.hook, "hashtags": script.hashtags,
        "slides": [s.model_dump() for s in slides],
        "source_note": source.to_dict(),
    }
    (out_dir / "script.json").write_text(json.dumps(script_data, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "evidence.json").write_text(json.dumps(episode.get("evidence", {}), ensure_ascii=False, indent=2), encoding="utf-8")
    return out_dir
