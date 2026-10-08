"""The source attribution shown on a card and in its caption.

A card news account is only as believable as the sources it names. Until now
the source URL sat in meta.json and never reached the post, so readers could not
tell a fresh news article from a year-old vendor report. This module builds one
`SourceNote` (outlet, article title, date, link) from the verified source
lineage; the renderer draws it on the closing card and the caption repeats it.

Dates are shown only when actually known — a missing date is left out, never
guessed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from urllib.parse import urlsplit

# Registered domain -> display name. Anything else falls back to the domain.
_OUTLETS: dict[str, str] = {
    "techcrunch.com": "TechCrunch",
    "theverge.com": "The Verge",
    "wired.com": "WIRED",
    "arstechnica.com": "Ars Technica",
    "venturebeat.com": "VentureBeat",
    "engadget.com": "Engadget",
    "zdnet.com": "ZDNET",
    "cnet.com": "CNET",
    "reuters.com": "Reuters",
    "bloomberg.com": "Bloomberg",
    "cnbc.com": "CNBC",
    "bbc.com": "BBC",
    "bbc.co.uk": "BBC",
    "nytimes.com": "The New York Times",
    "wsj.com": "The Wall Street Journal",
    "ft.com": "Financial Times",
    "theinformation.com": "The Information",
    "9to5mac.com": "9to5Mac",
    "9to5google.com": "9to5Google",
    "technologyreview.com": "MIT Technology Review",
    "openai.com": "OpenAI",
    "anthropic.com": "Anthropic",
    "blog.google": "Google",
    "microsoft.com": "Microsoft",
    "nvidia.com": "NVIDIA",
    "apple.com": "Apple",
    "samsungsds.com": "삼성SDS",
    "zdnet.co.kr": "ZDNet Korea",
    "etnews.com": "전자신문",
    "mk.co.kr": "매일경제",
    "hankyung.com": "한국경제",
    "chosun.com": "조선일보",
    "joongang.co.kr": "중앙일보",
    "yna.co.kr": "연합뉴스",
    "bloter.net": "블로터",
    "aitimes.com": "AI타임스",
    "itworld.co.kr": "ITWorld Korea",
}

_TITLE_MAX = 90
# A link cut in the middle cannot be used, so a long one is left out entirely.
_URL_MAX = 140
_SEPARATORS = re.compile(r"\s+[|–—]\s+|\s+-\s+")


def _registered_domain(host: str) -> str:
    host = host.lower().split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    parts = host.split(".")
    if len(parts) <= 2:
        return host
    # "bbc.co.uk"-style two-part public suffixes keep three labels.
    if len(parts[-1]) == 2 and parts[-2] in {"co", "com", "or", "ne", "go", "ac"}:
        return ".".join(parts[-3:])
    # A mapped sub-domain wins ("blog.google"); otherwise use the registered domain.
    candidate = ".".join(parts[-2:])
    return candidate


def outlet_from_url(url: str) -> str:
    """Display name for the publisher behind `url`."""

    try:
        host = urlsplit(url).netloc
    except ValueError:
        return ""
    if not host:
        return ""
    plain = host.lower().split(":")[0].removeprefix("www.")
    if plain in _OUTLETS:
        return _OUTLETS[plain]
    domain = _registered_domain(host)
    if domain in _OUTLETS:
        return _OUTLETS[domain]
    # Unmapped: "example-news.com" -> "Example-news".
    return domain.split(".")[0].replace("_", " ").capitalize()


def clean_title(title: str, outlet: str = "") -> str:
    """The article's own headline, without a trailing "| Site name" or length overflow."""

    text = re.sub(r"\s+", " ", title or "").strip()
    if not text:
        return ""
    parts = [part.strip() for part in _SEPARATORS.split(text) if part.strip()]
    head = parts[0] if parts else text
    # A very short first segment is usually the site name or section, not the headline.
    if len(head) < 8 and len(parts) > 1:
        head = max(parts, key=len)
    if len(head) > _TITLE_MAX:
        # Cut at a word boundary so the headline never ends mid-word ("agen…").
        cut = head[: _TITLE_MAX - 1]
        boundary = cut.rfind(" ")
        if boundary >= _TITLE_MAX // 2:
            cut = cut[:boundary]
        head = cut.rstrip(" ,;:-–—") + "…"
    return head


def _short_url(url: str) -> str:
    """The link without scheme or tracking parameters; "" if it would not fit whole."""

    try:
        parts = urlsplit(url)
    except ValueError:
        return ""
    if not parts.netloc:
        return ""
    plain = f"{parts.netloc.removeprefix('www.')}{parts.path}".rstrip("/")
    return plain if len(plain) <= _URL_MAX else ""


@dataclass(frozen=True)
class SourceNote:
    outlet: str
    title: str
    url: str
    published: date | None = None

    def date_text(self) -> str:
        return self.published.strftime("%Y.%m.%d") if self.published else ""

    def card_lines(self) -> list[str]:
        """Lines for the closing card: outlet, headline, then the date."""

        lines = [self.outlet] if self.outlet else []
        if self.title:
            lines.append(self.title)
        if self.published:
            lines.append(f"{self.date_text()} 발행")
        return lines

    def caption_block(self) -> str:
        """The attribution paragraph appended to the Instagram caption."""

        head = f"출처: {self.outlet}" if self.outlet else "출처"
        if self.published:
            head = f"{head} · {self.date_text()}"
        lines = [head]
        if self.title:
            lines.append(f"「{self.title}」")
        link = _short_url(self.url)
        if link:
            lines.append(f"원문: {link}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "outlet": self.outlet,
            "title": self.title,
            "url": self.url,
            "published": self.published.isoformat() if self.published else "",
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> "SourceNote | None":
        if not isinstance(data, dict) or not (data.get("outlet") or data.get("title")):
            return None
        from src.qa.source_freshness import parse_date

        return cls(
            outlet=str(data.get("outlet") or ""),
            title=str(data.get("title") or ""),
            url=str(data.get("url") or ""),
            published=parse_date(str(data.get("published") or "")),
        )


def from_lineage(lineage, *, fetch_missing_date: bool = True) -> SourceNote | None:
    """Build the note from a verified SourceLineage.

    Rows queued before the publication date was recorded have none, so the page
    is asked once (best-effort); a date that still cannot be read stays blank.
    """

    if lineage is None:
        return None
    url = str(getattr(lineage, "source_url", "") or "")
    title = clean_title(str(getattr(lineage, "source_title", "") or ""))
    outlet = outlet_from_url(url)
    if not (outlet or title):
        return None

    from src.qa.source_freshness import fetch_published_date, parse_date

    published = parse_date(str(getattr(lineage, "published_at", "") or ""))
    if published is None and fetch_missing_date and url:
        published = fetch_published_date(url)
    return SourceNote(outlet=outlet, title=title, url=url, published=published)


def insert_into_caption(caption: str, block: str, hashtag_text: str = "") -> str:
    """Put the attribution before the trailing hashtags, or at the end if none."""

    if not block:
        return caption
    body = caption.rstrip()
    tags = hashtag_text.strip()
    if tags and body.endswith(tags):
        head = body[: -len(tags)].rstrip()
        return f"{head}\n\n{block}\n\n{tags}"
    return f"{body}\n\n{block}"
