from datetime import date

from src import token_status as t


def test_days_left_and_banner(monkeypatch):
    monkeypatch.delenv("IG_TOKEN_REFRESHED", raising=False)
    assert t.days_left() is None and "기록되어" in t.banner_html()
    monkeypatch.setenv("IG_TOKEN_REFRESHED", "2026-10-20")
    assert t.days_left(date(2026, 10, 20)) == 60
    assert t.banner_html(date(2026, 10, 25)) == ""
    assert "5일 남았" in t.banner_html(date(2026, 12, 14))
    assert "만료되었" in t.banner_html(date(2026, 12, 25))


def test_threads_banner_is_silent_until_configured(monkeypatch):
    monkeypatch.delenv("THREADS_ACCESS_TOKEN", raising=False)
    assert t.threads_banner_html() == ""
    monkeypatch.setenv("THREADS_ACCESS_TOKEN", "x")
    monkeypatch.setenv("THREADS_TOKEN_REFRESHED", "2026-10-20")
    assert t.threads_banner_html(date(2026, 10, 25)) == ""
    assert "3일 남았" in t.threads_banner_html(date(2026, 12, 16))
