import json
from datetime import datetime, timedelta, timezone

from src.qa import candidate_score as cs

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)

RICH = (
    "OpenAI expanded ChatGPT ads to 26 more countries, bringing the total to 89 countries. "
    "The company said ads reach 120 million users, up 35% from March. Google and Meta also "
    "run ad platforms. " * 12
)


def _meta(content, published, topic="OpenAI, 89개국으로 확대", source="AI타임스"):
    return {
        "topic": topic,
        "source_url": "https://www.aitimes.com/news/1",
        "evidence": [{"content": content, "published_at": published, "source": source}],
    }


def test_rich_fresh_article_beats_thin_old_one():
    good = cs.score_candidate(_meta(RICH, (NOW - timedelta(hours=20)).isoformat()), now=NOW)
    thin = cs.score_candidate(
        _meta("AI is changing everything soon. " * 10, (NOW - timedelta(days=5)).isoformat(), topic="AI 전망"),
        now=NOW,
    )
    assert good.total > thin.total + 30
    assert good.parts["freshness"] == 20 and thin.parts["freshness"] == 0


def test_unknown_date_scores_zero_freshness_and_says_so():
    s = cs.score_candidate(_meta(RICH, ""), now=NOW)
    assert s.parts["freshness"] == 0 and "발행일 불명" in s.reasons


def test_markdown_noise_is_not_counted_as_body():
    noisy = "![logo](https://cdn.x/logo.png) " * 50 + "짧은 본문"
    assert len(cs.clean_text(noisy)) < 20


def test_years_are_not_counted_as_figures():
    assert cs._figures("In 2026 the plan, announced 2025년, had no numbers") == 0
    assert cs._figures("It reached 89 countries and 35% growth") == 2


def test_video_signal_only_when_probed():
    meta = _meta(RICH, (NOW - timedelta(hours=5)).isoformat())
    assert cs.score_candidate(meta, now=NOW).parts["video"] == 0
    probed = cs.score_candidate(meta, now=NOW, video_count=2)
    assert probed.parts["video"] == 10 and any("영상 후보 2개" in r for r in probed.reasons)


def test_best_queue_id_skips_failed_rows_and_prefers_higher_score(monkeypatch):
    monkeypatch.setattr(cs, "probe_video_count", lambda *a, **k: None)
    fresh = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
    rows = [
        {"id": 1, "status": "pending", "publish_error_code": "RUN_BUDGET_EXCEEDED",
         "metadata_json": json.dumps(_meta(RICH, fresh))},
        {"id": 2, "status": "pending", "publish_error_code": None,
         "metadata_json": json.dumps(_meta("짧음", fresh, topic="AI"))},
        {"id": 3, "status": "pending", "publish_error_code": None,
         "metadata_json": json.dumps(_meta(RICH, fresh))},
        {"id": 4, "status": "published", "publish_error_code": None,
         "metadata_json": json.dumps(_meta(RICH, fresh))},
    ]
    assert cs.best_queue_id(rows) == 3
    assert cs.best_queue_id([rows[0], rows[3]]) is None
