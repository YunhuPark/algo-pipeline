"""Topic categories, the trend signal, and the growth report by category."""
import sqlite3
from datetime import date, datetime

import pytest

from src import growth_tracker as gt
from src.agents import news_collector as nc
from src.agents import trend_signal as ts
from src.qa.topic_category import GENERAL, categorize, primary


# ── categories ─────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("EU, AI 규제법안 통과… 과징금 최대 7%", "규제·정책"),
    ("오픈AI, 기업가치 1,500억 달러 투자 유치", "투자·돈"),
    ("AI 도입으로 신입 채용 줄고 감원 확산", "일자리"),
    ("AI 데이터센터 전력 수요 급증, 탄소 배출 논란", "환경·전력"),
    ("AI 활용 랜섬웨어 조직 14개국 공격", "보안·개인정보"),
    ("앤트로픽, 신약 개발사 인수", "투자·돈"),            # money outranks health in priority order
    ("AI 의료 진단 보조, 병원 도입", "교육·의료"),
])
def test_categories_follow_the_keywords(text, expected):
    assert primary(text) == expected


def test_a_story_can_carry_two_categories_and_unmatched_text_is_general():
    assert categorize("AI 규제법안과 투자 위축 우려") == ["규제·정책", "투자·돈"]
    assert categorize("새 모델 벤치마크 공개") == [GENERAL]
    assert categorize("") == [GENERAL]


# ── trend signal ───────────────────────────────────────────

FEED = """<rss><channel>
<item><title>비트코인 시세</title><ht:approx_traffic>100+</ht:approx_traffic></item>
<item><title><![CDATA[엔비디아]]></title><ht:approx_traffic>500+</ht:approx_traffic></item>
<item><title>A</title></item>
</channel></rss>"""


def test_the_feed_is_parsed_into_terms_with_traffic():
    assert ts.parse_terms(FEED) == [("비트코인 시세", "100+"), ("엔비디아", "500+"), ("A", "")]
    assert ts.parse_terms("") == []


def test_matches_ignore_one_character_terms_and_case():
    assert ts.matches("엔비디아, 새 GPU 공개", ["엔비디아", "A", "삼성"]) == ["엔비디아"]
    assert ts.matches("OpenAI launches", ["openai"]) == ["openai"]


def test_an_unreadable_feed_means_no_signal_not_an_error(monkeypatch):
    def boom(*a, **k):
        raise ts.httpx.ConnectError("down")

    monkeypatch.setattr(ts.httpx, "get", boom)
    monkeypatch.setitem(ts._cache, "terms", [])
    assert ts.fetch_terms(force=True) == []


# ── the selector hints ─────────────────────────────────────

def _item(title, summary=""):
    return nc.NewsItem(title=title, summary=summary, source="x", url="https://x.test/a", published=datetime(2026, 10, 30))


def test_the_selector_sees_category_and_trend_hints_but_only_when_there_is_something_to_say():
    tags = nc._signal_tags(_item("엔비디아, AI 데이터센터 전력 문제 해결책 공개"), ["엔비디아", "삼성"])
    assert "[분야: 환경·전력]" in tags and "[트렌드: 엔비디아]" in tags
    assert nc._signal_tags(_item("새 모델 벤치마크 공개"), ["삼성"]) == ""


def test_the_prompt_tells_the_selector_the_hints_are_soft():
    assert "참고 신호" in nc._HUMAN and "근거와 수치가 약한 기사를 고르지는 마십시오" in nc._HUMAN


# ── growth report by category ──────────────────────────────

def test_the_growth_report_groups_published_posts_by_category(tmp_path):
    db = tmp_path / "g.db"
    with sqlite3.connect(db) as conn:
        conn.executescript(gt.SCHEMA)
        for platform, post_id, reach in (("instagram", "p1", 10), ("instagram", "p2", 0), ("instagram", "p3", 4)):
            conn.execute(
                "insert into post_snapshots(day, platform, post_id, reach, views, likes, saves, shares, captured_at) "
                "values (?,?,?,?,?,?,?,?,?)", ("2026-10-30", platform, post_id, reach, reach, 1, 0, 0, "t"),
            )
        topics = {
            ("instagram", "p1"): "EU AI 규제법안 통과",
            ("instagram", "p2"): "새 모델 벤치마크 공개",
            ("instagram", "p3"): "AI 규제 소송 확산",
        }
        text = gt.category_report(conn, topics)
    assert "규제·정책: 2개 / 평균 7.0" in text
    assert "일반: 1개 / 평균 0.0" in text
    with sqlite3.connect(tmp_path / "empty.db") as conn:
        assert gt.category_report(conn, {}) == ""


def test_security_stories_are_tagged_but_not_favoured_by_the_selector_prompt():
    # hacking/security news only has to be in the pool; it gets no preference over other topics
    assert "보안" not in nc._HUMAN.split("참고 신호")[1]
    assert "환경·전력" in nc._HUMAN
