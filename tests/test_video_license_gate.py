"""A source credit is not a licence. Only videos whose owner clearly allowed reuse may go on a card."""
from types import SimpleNamespace

import pytest

from src.agents import youtube_fetcher as yf


def _meta(monkeypatch, **fields):
    monkeypatch.setattr(yf, "_get_video_metadata", lambda video_id: fields)


@pytest.mark.parametrize("license_text", [
    "Creative Commons Attribution license (reuse allowed)",
    "creative commons attribution license",
])
def test_a_creative_commons_video_is_permitted(monkeypatch, license_text):
    _meta(monkeypatch, license=license_text, channel="Some Channel")
    ok, why = yf.video_use_permitted("v1")
    assert ok and "라이선스" in why


@pytest.mark.parametrize("license_text", ["", "Standard YouTube License", "youtube"])
def test_a_standard_or_unknown_license_is_not_permitted(monkeypatch, license_text):
    _meta(monkeypatch, license=license_text, channel="Some Channel")
    ok, why = yf.video_use_permitted("v1")
    assert not ok and "재사용 허락 없음" in why


def test_a_missing_metadata_is_not_permitted(monkeypatch):
    _meta(monkeypatch)                         # the lookup failed: nothing proves permission
    assert yf.video_use_permitted("v1")[0] is False


def test_a_channel_you_chose_is_permitted_even_with_the_standard_license(monkeypatch):
    _meta(monkeypatch, license="Standard YouTube License", channel="Official Press Channel")
    monkeypatch.setattr(yf, "_allowed_channels", lambda: {"official press channel"})
    ok, why = yf.video_use_permitted("v1")
    assert ok and "허락된 채널" in why
    monkeypatch.setattr(yf, "_allowed_channels", lambda: set())
    assert yf.video_use_permitted("v1")[0] is False


def test_the_allow_list_comes_from_the_env_file(tmp_path):
    env = tmp_path / ".env"
    env.write_text("VIDEO_ALLOWED_CHANNELS= Alpha ,Beta Channel," + chr(10), encoding="utf-8")
    assert yf._allowed_channels(env) == {"alpha", "beta channel"}
    assert yf._allowed_channels(tmp_path / "missing.env") == set()      # no file, no allowed channels


def test_a_video_without_permission_is_dropped_before_any_subtitle_request(monkeypatch):
    video = yf.VideoInfo(
        video_id="vidNoPerm01", url="youtu.be/vidNoPerm01", title="Amazon robots in the warehouse",
        creator="Some Channel", snippet="Amazon robot warehouse",
    )
    requested = []
    monkeypatch.setattr(yf, "video_use_permitted", lambda _id: (False, "재사용 허락 없음(테스트)"))
    monkeypatch.setattr(yf, "_get_video_transcript", lambda _id: requested.append(_id) or "x " * 400)
    monkeypatch.setattr(yf, "_search_youtube", lambda *a, **k: [video])
    monkeypatch.setattr(yf, "_validate_candidates", lambda c, **k: c)
    monkeypatch.setattr(yf, "_generate_search_keywords", lambda *a, **k: ["amazon robots"])

    class _LLM:
        def invoke(self, *_a, **_k):
            return SimpleNamespace(content="[1]")

    monkeypatch.setattr("langchain_openai.ChatOpenAI", lambda *a, **k: _LLM())
    found, start = yf.find_verified_video_for_slide(
        slide_title="아마존 로봇 운영", slide_body="아마존은 100만 대 이상의 로봇을 운영합니다.",
        topic="Amazon robots", candidates=[video], article_title="Amazon robots arms race",
    )
    assert found is None and start == 0
    assert requested == []                      # no subtitle request was made for a video we may not use
