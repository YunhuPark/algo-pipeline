"""
텔레그램 봇 — 폰에서 카드뉴스 관리
────────────────────────────────────────────────────────
python-telegram-bot v20 (asyncio) 사용.

공개 함수:
  send_cards_for_approval(image_paths, script, pipeline_callback)
  start_bot()      — 봇 폴링 시작 (blocking)
  notify(text)     — 단순 텍스트 알림

환경변수 (.env):
  TELEGRAM_BOT_TOKEN
  TELEGRAM_CHAT_ID
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable, Awaitable

from dotenv import load_dotenv
from telegram import (
    Bot,
    InputMediaPhoto,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from src.db import get_posts, queue_count, get_queue, mark_queue_status

load_dotenv()

logger = logging.getLogger(__name__)

# ── 환경변수 ──────────────────────────────────────────────
TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

# ── 전역: 승인 대기 상태 저장 ────────────────────────────
# key: callback_data prefix ("approve_<uuid>") → value: pipeline_callback
_pending_approvals: dict[str, Callable] = {}

# 봇 애플리케이션 인스턴스 (start_bot 호출 후 설정)
_app: Application | None = None
_loop: asyncio.AbstractEventLoop | None = None


# ── 헬퍼 ──────────────────────────────────────────────────

def _chat_id() -> int:
    if not CHAT_ID:
        raise EnvironmentError("TELEGRAM_CHAT_ID가 .env에 없습니다.")
    return int(CHAT_ID)


def _today_count() -> int:
    today = datetime.now().strftime("%Y-%m-%d")
    rows = get_posts(limit=200)
    return sum(1 for r in rows if str(r["posted_at"]).startswith(today))


def _recent_summary(limit: int = 5) -> str:
    rows = get_posts(limit=limit)
    if not rows:
        return "게시물 없음"
    lines = []
    for r in rows:
        date = str(r["posted_at"])[:10]
        lines.append(f"  • [{date}] {r['topic']} ({r['platform']})")
    return "\n".join(lines)


# ── 커맨드 핸들러 ─────────────────────────────────────────

async def _cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "안녕하세요! 알고 카드뉴스 봇입니다 👋\n\n"
        "사용 가능한 명령어:\n"
        "  /status       — 큐 현황 및 최근 성과\n"
        "  /generate [주제] — 카드뉴스 즉시 생성\n"
        "  /queue        — 현재 큐 목록\n"
        "  /skip         — 큐 맨 앞 항목 건너뜀\n"
    )
    await update.message.reply_text(text)


async def _cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    pending = queue_count("pending")
    ready = queue_count("ready")
    published = queue_count("published")
    today = _today_count()
    summary = _recent_summary(5)

    text = (
        "📊 현재 상태\n\n"
        f"큐 대기: {pending}개\n"
        f"큐 준비: {ready}개\n"
        f"발행 완료: {published}개\n"
        f"오늘 게시물: {today}개\n\n"
        f"최근 게시물:\n{summary}"
    )
    await update.message.reply_text(text)


async def _cmd_generate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args
    topic = " ".join(args).strip() if args else ""

    if not topic:
        await update.message.reply_text(
            "주제를 입력해주세요.\n예: /generate AI 스타트업 투자 트렌드"
        )
        return

    await update.message.reply_text(f"생성을 시작합니다: {topic}\n잠시 기다려 주세요...")

    def _run():
        try:
            from src import pipeline
            from src.persona import load_persona

            persona = load_persona()
            paths = pipeline.run_pipeline(
                topic=topic,
                persona=persona,
                auto=True,
            )
            if paths:
                # 생성 완료 → 승인 요청 전송
                asyncio.run_coroutine_threadsafe(
                    send_cards_for_approval(
                        image_paths=paths,
                        script=None,
                        pipeline_callback=None,
                    ),
                    _loop,
                )
            else:
                asyncio.run_coroutine_threadsafe(
                    _bot().send_message(
                        chat_id=_chat_id(),
                        text=f"'{topic}' 생성 실패 — 파이프라인이 빈 결과를 반환했습니다.",
                    ),
                    _loop,
                )
        except Exception as exc:
            logger.exception("generate 오류")
            asyncio.run_coroutine_threadsafe(
                _bot().send_message(
                    chat_id=_chat_id(),
                    text=f"생성 중 오류 발생: {exc}",
                ),
                _loop,
            )

    threading.Thread(target=_run, daemon=True).start()


async def _cmd_queue(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rows = get_queue("pending")
    if not rows:
        await update.message.reply_text("대기 중인 큐가 없습니다.")
        return

    lines = ["📋 큐 목록 (pending)\n"]
    for i, r in enumerate(rows[:15], 1):
        sched = f" [{r['scheduled_at'][:16]}]" if r["scheduled_at"] else ""
        lines.append(f"  {i}. {r['topic']}{sched}")

    await update.message.reply_text("\n".join(lines))


async def _cmd_skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from src.db import dequeue_next

    row = dequeue_next()
    if not row:
        await update.message.reply_text("건너뜀 항목이 없습니다.")
        return

    mark_queue_status(row["id"], "skipped")
    await update.message.reply_text(f"건너뜀: {row['topic']}")


# ── 인라인 버튼 콜백 ──────────────────────────────────────

async def _callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    data: str = query.data  # "upload:<key>" / "retry:<key>" / "cancel:<key>"
    if ":" not in data:
        return

    action, key = data.split(":", 1)
    cb = _pending_approvals.pop(key, None)

    if action == "upload":
        await query.edit_message_reply_markup(reply_markup=None)
        await _bot().send_message(chat_id=_chat_id(), text="✅ 업로드를 시작합니다...")
        if cb:
            threading.Thread(target=lambda: cb("upload"), daemon=True).start()

    elif action == "retry":
        await query.edit_message_reply_markup(reply_markup=None)
        await _bot().send_message(chat_id=_chat_id(), text="🔄 재생성을 시작합니다...")
        if cb:
            threading.Thread(target=lambda: cb("retry"), daemon=True).start()

    elif action == "cancel":
        await query.edit_message_reply_markup(reply_markup=None)
        await _bot().send_message(chat_id=_chat_id(), text="❌ 취소되었습니다.")
        if cb:
            threading.Thread(target=lambda: cb("cancel"), daemon=True).start()


# ── 공개 API ──────────────────────────────────────────────

def _bot() -> Bot:
    if not TOKEN:
        raise EnvironmentError("TELEGRAM_BOT_TOKEN이 .env에 없습니다.")
    return Bot(token=TOKEN)


async def send_cards_for_approval(
    image_paths: list[Path],
    script,
    pipeline_callback: Callable | None,
) -> None:
    """
    렌더링된 PNG 이미지들을 텔레그램으로 전송하고 승인 버튼을 붙입니다.

    Args:
        image_paths:       렌더링된 PNG Path 목록
        script:            CardNewsScript (caption 추출용, None 가능)
        pipeline_callback: 버튼 탭 시 호출될 콜백 (action: str) → None
    """
    if not image_paths:
        await _bot().send_message(chat_id=_chat_id(), text="전송할 이미지가 없습니다.")
        return

    # 최대 10장 (Telegram media group 제한)
    paths = image_paths[:10]

    # ── 미디어 그룹 전송 ──────────────────────────────────
    media: list[InputMediaPhoto] = []
    for i, p in enumerate(paths):
        caption = None
        if i == 0 and script is not None:
            caption = getattr(script, "hook", None) or getattr(script, "title", None)
        with open(p, "rb") as f:
            media.append(InputMediaPhoto(media=f.read(), caption=caption or ""))

    await _bot().send_media_group(chat_id=_chat_id(), media=media)

    # ── 승인 키보드 전송 ──────────────────────────────────
    import uuid
    key = uuid.uuid4().hex
    if pipeline_callback:
        _pending_approvals[key] = pipeline_callback

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ 업로드", callback_data=f"upload:{key}"),
            InlineKeyboardButton("🔄 재생성", callback_data=f"retry:{key}"),
            InlineKeyboardButton("❌ 취소",   callback_data=f"cancel:{key}"),
        ]
    ])
    topic_label = ""
    if script is not None:
        topic_label = f"\n주제: {getattr(script, 'title', '')}"

    await _bot().send_message(
        chat_id=_chat_id(),
        text=f"카드뉴스 생성 완료{topic_label}\n어떻게 하시겠습니까?",
        reply_markup=keyboard,
    )


def notify(text: str) -> None:
    """단순 텍스트 알림 전송 (동기 래퍼)."""
    async def _send():
        await _bot().send_message(chat_id=_chat_id(), text=text)

    if _loop and _loop.is_running():
        asyncio.run_coroutine_threadsafe(_send(), _loop)
    else:
        asyncio.run(_send())


def start_bot() -> None:
    """
    봇 폴링 시작 (blocking).
    스케줄러와 함께 사용할 때는 별도 스레드에서 호출하세요.
    """
    global _app, _loop

    if not TOKEN:
        raise EnvironmentError("TELEGRAM_BOT_TOKEN이 .env에 없습니다.")

    _loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_loop)

    _app = (
        Application.builder()
        .token(TOKEN)
        .build()
    )

    _app.add_handler(CommandHandler("start",    _cmd_start))
    _app.add_handler(CommandHandler("status",   _cmd_status))
    _app.add_handler(CommandHandler("generate", _cmd_generate))
    _app.add_handler(CommandHandler("queue",    _cmd_queue))
    _app.add_handler(CommandHandler("skip",     _cmd_skip))
    _app.add_handler(CallbackQueryHandler(_callback_handler))

    logger.info("[TelegramBot] 폴링 시작...")
    _app.run_polling(allowed_updates=Update.ALL_TYPES)
