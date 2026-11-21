import sqlite3
from datetime import date

from src import growth_tracker as gt


def _fake_get(url: str, params: dict) -> dict:
    """Canned platform answers; one Instagram metric fails to show that it never aborts the run."""
    if url.endswith("/me") and "instagram" in url:
        return {"followers_count": 2, "media_count": 46}
    if url.endswith("/me/media"):
        return {"data": [{"id": "ig1", "timestamp": "2026-10-23T05:52:35+0000", "permalink": "https://i/p/1", "like_count": 1, "comments_count": 0}]}
    if "/ig1/insights" in url:
        if params["metric"] == "shares":
            return {"error": {"message": "unsupported"}}
        return {"data": [{"name": params["metric"], "values": [{"value": {"reach": 5, "views": 9, "saved": 2}[params["metric"]]}]}]}
    if url.endswith("/me/threads_insights"):
        return {"data": [{"name": "followers_count", "total_value": {"value": 1}}]}
    if url.endswith("/me/threads"):
        return {"data": [{"id": "th1", "timestamp": "2026-10-29T02:22:21+0000", "permalink": "https://t/1"}]}
    if "/th1/insights" in url:
        return {"data": [{"name": "views", "values": [{"value": 4}]}, {"name": "likes", "values": [{"value": 1}]},
                         {"name": "replies", "values": [{"value": 0}]}]}
    return {"error": {"message": "unexpected " + url}}


def test_collect_reads_followers_and_posts_and_tolerates_a_failing_metric():
    data = gt.collect(date(2026, 10, 29), "igtok", "thtok", "123", get=_fake_get)
    assert ("instagram", 2, 46) in data["accounts"] and ("threads", 1, None) in data["accounts"]
    ig = next(p for p in data["posts"] if p["platform"] == "instagram")
    assert (ig["reach"], ig["views"], ig["saves"], ig["likes"]) == (5, 9, 2, 1)
    assert ig["shares"] is None                                  # the unreadable metric stays empty
    th = next(p for p in data["posts"] if p["platform"] == "threads")
    assert (th["views"], th["likes"], th["replies"]) == (4, 1, 0)


def test_a_missing_credential_is_noted_not_fatal():
    data = gt.collect(date(2026, 10, 29), "", "", "", get=_fake_get)
    assert data["accounts"] == [] and data["posts"] == []
    assert len(data["notes"]) == 2


def test_running_twice_in_a_day_updates_instead_of_duplicating(tmp_path):
    db = tmp_path / "growth.db"
    for followers in (2, 3):
        data = gt.collect(date(2026, 10, 29), "igtok", "thtok", "123", get=_fake_get)
        data["accounts"] = [("instagram", followers, 46)]
        with sqlite3.connect(db) as conn:
            gt.save(conn, date(2026, 10, 29), data)
    with sqlite3.connect(db) as conn:
        assert conn.execute("select count(*), max(followers) from account_snapshots").fetchone() == (1, 3)
        assert conn.execute("select count(*) from post_snapshots where platform='instagram'").fetchone()[0] == 1
    assert gt.has_today(db, date(2026, 10, 29)) and not gt.has_today(db, date(2026, 10, 30))


def test_the_report_shows_the_follower_trend_and_the_latest_posts(tmp_path):
    db = tmp_path / "growth.db"
    with sqlite3.connect(db) as conn:
        for day, followers in ((date(2026, 10, 29), 2), (date(2026, 11, 5), 5)):
            data = gt.collect(day, "igtok", "thtok", "123", get=_fake_get)
            data["accounts"] = [("instagram", followers, 46)]
            gt.save(conn, day, data)
        text = gt.report(conn)
    assert "2026-10-29 ~ 2026-11-05" in text
    assert "팔로워 instagram: 2 → 5 (+3)" in text
    assert "도달 5" in text and "조회 9" in text and "저장 2" in text
    assert "공유" not in text.split("instagram 최근 게시물")[1].split("threads")[0]      # no number was read


def test_an_empty_database_explains_how_to_start(tmp_path):
    with sqlite3.connect(tmp_path / "empty.db") as conn:
        assert "첫 기록" in gt.report(conn)


def test_the_wrong_threads_account_is_never_read(tmp_path, monkeypatch):
    from src import threads_credentials as creds

    seen = []
    monkeypatch.setattr(creds, "load", lambda: ("tok", "999"))
    monkeypatch.setattr(creds, "assert_expected_account", lambda t, u: (_ for _ in ()).throw(ValueError("다른 계정")))
    monkeypatch.setattr(gt, "ENV_PATH", tmp_path / "no.env")

    def spy(url, params):
        seen.append(url)
        return {"error": {"message": "x"}}

    data = gt.snapshot(date(2026, 10, 29), tmp_path / "g.db", get=spy)
    assert any("다른 계정" in n for n in data["notes"])
    assert not any("graph.threads.net" in u for u in seen)       # no Threads call was made
