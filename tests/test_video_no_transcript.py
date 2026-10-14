from __future__ import annotations

from types import SimpleNamespace

from src.agents import youtube_fetcher as yf


def test_video_without_transcript_is_never_accepted(monkeypatch):
    """A title/snippet that happens to share keywords must not attach a video.

    Regression: a game walkthrough was accepted for a chip article because short
    English tokens matched as substrings, with no transcript to check.
    """

    video = yf.VideoInfo(
        video_id="gameVid0001",
        url="youtu.be/gameVid0001",
        title="Unicorn Kingdom Full Gameplay Walkthrough",
        creator="GoGy Games",
        snippet="AI Microsoft chip Maia 300 Nvidia gameplay",
    )
    monkeypatch.setattr(yf, "_get_video_transcript", lambda _id: "")
    monkeypatch.setattr(yf, "_search_youtube", lambda *a, **k: [video])
    monkeypatch.setattr(yf, "_validate_candidates", lambda c, **k: c)
    monkeypatch.setattr(yf, "_generate_search_keywords", lambda *a, **k: ["maia 300"])

    class _LLM:
        def invoke(self, *_a, **_k):
            return SimpleNamespace(content="[1]")

    monkeypatch.setattr("langchain_openai.ChatOpenAI", lambda *a, **k: _LLM())

    found, start = yf.find_verified_video_for_slide(
        slide_title="Maia 300의 역할",
        slide_body="Maia 300은 Microsoft의 AI 칩입니다.",
        topic="Microsoft Maia 300 chip",
        candidates=[video],
        article_title="Microsoft's new AI chip makes it less reliant on Nvidia's GPUs",
    )
    assert found is None and start == 0


def _verified_slide_search(monkeypatch, moving: bool):
    video = yf.VideoInfo(
        video_id="vid00000001",
        url="youtu.be/vid00000001",
        title="Amazon robots in the warehouse",
        creator="Some Channel",
        snippet="Amazon robot warehouse",
    )
    monkeypatch.setattr(yf, "_get_video_transcript", lambda _id: "Amazon robots move packages in the warehouse " * 20)
    monkeypatch.setattr(yf, "_search_youtube", lambda *a, **k: [video])
    monkeypatch.setattr(yf, "_validate_candidates", lambda c, **k: c)
    monkeypatch.setattr(yf, "_generate_search_keywords", lambda *a, **k: ["amazon robots"])
    monkeypatch.setattr(yf, "_has_motion_at", lambda vid, start: moving)

    class _LLM:
        def invoke(self, prompt, *_a, **_k):
            text = str(prompt)
            if "관련 가능성 있는 번호" in text:
                return SimpleNamespace(content="[1]")
            return SimpleNamespace(content='{"match": true, "start_seconds": 12, "reason": "ok"}')

    monkeypatch.setattr("langchain_openai.ChatOpenAI", lambda *a, **k: _LLM())
    return yf.find_verified_video_for_slide(
        slide_title="아마존 로봇 운영",
        slide_body="아마존은 100만 대 이상의 로봇을 운영합니다.",
        topic="Amazon robots",
        candidates=[video],
        article_title="Amazon robots arms race",
    )


def test_a_matching_but_frozen_video_is_skipped_so_another_candidate_can_be_tried(monkeypatch):
    found, _start = _verified_slide_search(monkeypatch, moving=False)
    assert found is None


def test_a_matching_moving_video_is_used(monkeypatch):
    found, start = _verified_slide_search(monkeypatch, moving=True)
    assert found is not None and start == 12
