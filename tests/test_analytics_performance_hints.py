from __future__ import annotations

import pytest

from src import db
from src.agents.analytics import get_performance_hints


@pytest.fixture
def algo_db(tmp_path, monkeypatch):
    path = tmp_path / "algo-test.db"
    monkeypatch.setenv("ALGO_ENV", "test")
    monkeypatch.setenv("ALGO_DB_PATH", str(path))
    db.init_db(path)
    return path


def _seed_post(i: int, *, likes: int, comments: int, saves: int) -> None:
    post_id = f"post{i}"
    db.insert_post(
        platform="instagram",
        topic=f"주제{i}",
        post_id=post_id,
        angle="리스트형",
        hook=f"훅 문구 {i}",
    )
    db.insert_analytics(
        post_id=post_id,
        platform="instagram",
        likes=likes,
        comments=comments,
        saves=saves,
    )


def test_get_performance_hints_does_not_raise_on_real_rows(algo_db):
    """get_performance_hints() reads real sqlite3.Row objects from get_analytics().

    sqlite3.Row has no .get() method - calling r.get("hook") raises AttributeError
    and used to be silently swallowed by pipeline.py's broad except Exception,
    which meant analytics feedback silently never reached angle selection even
    though the data existed. This reproduces that exact call path."""
    for i in range(7):
        _seed_post(i, likes=10 + i, comments=i, saves=i)

    hints = get_performance_hints()

    assert hints != ""
    assert "성과 피드백" in hints
    assert "훅 문구" in hints


def test_get_performance_hints_empty_when_no_data(algo_db):
    assert get_performance_hints() == ""
