from __future__ import annotations

from datetime import datetime, timedelta

from src.agents.news_collector import NewsItem, _drop_stale_items


def _item(title: str, age_days: int | None) -> NewsItem:
    published = None if age_days is None else datetime.now() - timedelta(days=age_days)
    return NewsItem(title=title, summary="", source="x", url=f"https://x.com/{title}", published=published)


def test_old_dated_items_are_dropped_before_topic_selection():
    kept = _drop_stale_items([_item("old", 30), _item("new", 0), _item("undated", None)])
    assert [i.title for i in kept] == ["new", "undated"]


def test_all_stale_keeps_everything_so_the_later_gate_decides():
    items = [_item("a", 30), _item("b", 40)]
    assert _drop_stale_items(items) == items
