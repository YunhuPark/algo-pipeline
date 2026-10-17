from __future__ import annotations

import pytest

from src.agents.news_collector import _is_deal_title


@pytest.mark.parametrize(
    "title",
    [
        "Save big on REI and Barnes & Noble gift cards",
        "Nothing Headphone (a) is half off at Amazon",
        "Apple's HomeView smart display is $50 off",
        "The best Prime Day deals right now",
        "Google Play gift card discount this week",
        "삼성 갤럭시 반값 할인 특가",
    ],
)
def test_shopping_deals_are_excluded(title):
    assert _is_deal_title(title)


@pytest.mark.parametrize(
    "title",
    [
        "OpenAI cuts API prices for its flagship model",
        "Microsoft's new AI chip makes it less reliant on Nvidia's GPUs",
        "Topgolf saved $10 million by moving off VMware",
        "엔비디아, 차세대 AI 칩 가격 인하 발표",
    ],
)
def test_real_news_is_not_excluded(title):
    assert not _is_deal_title(title)
