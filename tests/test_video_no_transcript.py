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
