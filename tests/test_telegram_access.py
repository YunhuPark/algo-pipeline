import asyncio
import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


@pytest.mark.parametrize("handler", ["_cmd_status", "_cmd_queue", "_cmd_skip", "_cmd_generate", "_callback_handler"])
def test_untrusted_chat_or_user_cannot_reach_handlers(handler, monkeypatch):
    bot = importlib.import_module("src.telegram_bot")
    monkeypatch.setattr(bot, "CHAT_ID", "100")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_ID", "200")
    def forbidden(*args, **kwargs):
        pytest.fail("unauthorized update reached data/pipeline operation")
    for name in ("queue_count", "get_queue", "mark_queue_status", "get_posts"):
        monkeypatch.setattr(bot, name, forbidden)
    import src.db
    monkeypatch.setattr(src.db, "dequeue_next", forbidden)
    update = SimpleNamespace(effective_chat=SimpleNamespace(id=999),
                             effective_user=SimpleNamespace(id=999),
                             message=SimpleNamespace(reply_text=AsyncMock()),
                             callback_query=SimpleNamespace(answer=AsyncMock(), data="cancel:synthetic"))
    # /generate previously starts a new worker. Never allow a real worker here.
    monkeypatch.setattr(bot.threading, "Thread", forbidden)
    asyncio.run(getattr(bot, handler)(update, SimpleNamespace(args=["synthetic-topic"])))
    update.message.reply_text.assert_not_awaited()


def test_authorized_chat_user_can_read_queue(monkeypatch):
    bot = importlib.import_module("src.telegram_bot")
    monkeypatch.setattr(bot, "CHAT_ID", "100")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_ID", "200")
    monkeypatch.setattr(bot, "get_queue", lambda _: [])
    reply = AsyncMock()
    update = SimpleNamespace(effective_chat=SimpleNamespace(id=100),
                             effective_user=SimpleNamespace(id=200), message=SimpleNamespace(reply_text=reply))
    asyncio.run(bot._cmd_queue(update, None))
    reply.assert_awaited_once()
