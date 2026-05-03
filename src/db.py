"""
SQLite 데이터베이스 — 알고 Agent 영구 저장소
────────────────────────────────────────────────────────
테이블:
  posts       — 게시물 이력 (platform, topic, angle, post_id)
  analytics   — 성과 데이터 (likes, comments, saves, reach)
  queue       — 콘텐츠 큐 (예약 발행 대기 목록)
  competitors — 경쟁 계정 분석 데이터
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

DB_PATH = Path("data/algo.db")


def init_db() -> None:
    """DB 및 테이블 초기화 (최초 1회)."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS posts (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id     TEXT,                  -- 플랫폼 게시물 ID
            platform    TEXT NOT NULL,          -- instagram / threads / blog
            topic       TEXT NOT NULL,
            angle       TEXT DEFAULT '',        -- 사용된 마케팅 앵글
            hook        TEXT DEFAULT '',
            hashtags    TEXT DEFAULT '[]',      -- JSON array
            image_dir   TEXT DEFAULT '',        -- output/ 하위 폴더 경로
            posted_at   TEXT NOT NULL,
            created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS analytics (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id     TEXT NOT NULL,
            platform    TEXT NOT NULL,
            likes       INTEGER DEFAULT 0,
            comments    INTEGER DEFAULT 0,
            saves       INTEGER DEFAULT 0,
            reach       INTEGER DEFAULT 0,
            impressions INTEGER DEFAULT 0,
            checked_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS queue (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            topic        TEXT NOT NULL,
            context      TEXT DEFAULT '',
            angle_hint   TEXT DEFAULT '',
            image_dir    TEXT DEFAULT '',       -- 미리 렌더링된 경우 경로
            script_json  TEXT DEFAULT '',       -- CardNewsScript JSON
            status       TEXT DEFAULT 'pending', -- pending / ready / published / skipped
            scheduled_at TEXT,                  -- NULL이면 다음 차례
            created_at   TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS competitors (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            account      TEXT NOT NULL,
            post_id      TEXT DEFAULT '',
            likes        INTEGER DEFAULT 0,
            comments     INTEGER DEFAULT 0,
            topic        TEXT DEFAULT '',
            angle        TEXT DEFAULT '',
            pattern_note TEXT DEFAULT '',
            crawled_at   TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        );
        """)


@contextmanager
def _conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# ── posts ─────────────────────────────────────────────────

def insert_post(
    platform: str,
    topic: str,
    post_id: str = "",
    angle: str = "",
    hook: str = "",
    hashtags: list[str] | None = None,
    image_dir: str = "",
    posted_at: str | None = None,
) -> int:
    now = posted_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _conn() as conn:
        cur = conn.execute(
            """INSERT INTO posts (post_id, platform, topic, angle, hook, hashtags, image_dir, posted_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (post_id, platform, topic, angle, hook,
             json.dumps(hashtags or [], ensure_ascii=False), image_dir, now),
        )
        return cur.lastrowid


def get_posts(platform: str | None = None, limit: int = 50) -> list[sqlite3.Row]:
    with _conn() as conn:
        if platform:
            return conn.execute(
                "SELECT * FROM posts WHERE platform=? ORDER BY posted_at DESC LIMIT ?",
                (platform, limit),
            ).fetchall()
        return conn.execute(
            "SELECT * FROM posts ORDER BY posted_at DESC LIMIT ?", (limit,)
        ).fetchall()


# ── analytics ─────────────────────────────────────────────

def insert_analytics(
    post_id: str,
    platform: str,
    likes: int = 0,
    comments: int = 0,
    saves: int = 0,
    reach: int = 0,
    impressions: int = 0,
) -> None:
    with _conn() as conn:
        conn.execute(
            """INSERT INTO analytics (post_id, platform, likes, comments, saves, reach, impressions)
               VALUES (?,?,?,?,?,?,?)""",
            (post_id, platform, likes, comments, saves, reach, impressions),
        )


def get_analytics(platform: str = "instagram", limit: int = 30) -> list[sqlite3.Row]:
    with _conn() as conn:
        return conn.execute(
            """SELECT p.topic, p.angle, p.posted_at,
                      a.likes, a.comments, a.saves, a.reach, a.checked_at
               FROM analytics a
               JOIN posts p ON a.post_id = p.post_id AND a.platform = p.platform
               WHERE a.platform = ?
               ORDER BY a.checked_at DESC LIMIT ?""",
            (platform, limit),
        ).fetchall()


# ── queue ─────────────────────────────────────────────────

def enqueue(
    topic: str,
    context: str = "",
    angle_hint: str = "",
    image_dir: str = "",
    script_json: str = "",
    scheduled_at: str | None = None,
) -> int:
    with _conn() as conn:
        cur = conn.execute(
            """INSERT INTO queue (topic, context, angle_hint, image_dir, script_json, scheduled_at)
               VALUES (?,?,?,?,?,?)""",
            (topic, context, angle_hint, image_dir, script_json, scheduled_at),
        )
        return cur.lastrowid


def dequeue_next() -> sqlite3.Row | None:
    """다음 발행 대기 항목 (scheduled_at 기준, NULL이면 우선)."""
    with _conn() as conn:
        row = conn.execute(
            """SELECT * FROM queue
               WHERE status = 'pending'
                 AND (scheduled_at IS NULL OR scheduled_at <= datetime('now','localtime'))
               ORDER BY scheduled_at ASC NULLS FIRST, id ASC
               LIMIT 1""",
        ).fetchone()
        return row


def mark_queue_status(queue_id: int, status: str) -> None:
    with _conn() as conn:
        conn.execute("UPDATE queue SET status=? WHERE id=?", (status, queue_id))


def get_queue(status: str | None = None) -> list[sqlite3.Row]:
    with _conn() as conn:
        if status:
            return conn.execute(
                "SELECT * FROM queue WHERE status=? ORDER BY scheduled_at ASC NULLS FIRST, id ASC",
                (status,),
            ).fetchall()
        return conn.execute(
            "SELECT * FROM queue ORDER BY scheduled_at ASC NULLS FIRST, id ASC"
        ).fetchall()


def queue_count(status: str = "pending") -> int:
    with _conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM queue WHERE status=?", (status,)
        ).fetchone()[0]


# ── competitors ───────────────────────────────────────────

def insert_competitor(
    account: str,
    topic: str = "",
    angle: str = "",
    likes: int = 0,
    comments: int = 0,
    pattern_note: str = "",
    post_id: str = "",
) -> None:
    with _conn() as conn:
        conn.execute(
            """INSERT INTO competitors (account, post_id, likes, comments, topic, angle, pattern_note)
               VALUES (?,?,?,?,?,?,?)""",
            (account, post_id, likes, comments, topic, angle, pattern_note),
        )


def get_competitors(account: str | None = None, limit: int = 50) -> list[sqlite3.Row]:
    with _conn() as conn:
        if account:
            return conn.execute(
                "SELECT * FROM competitors WHERE account=? ORDER BY crawled_at DESC LIMIT ?",
                (account, limit),
            ).fetchall()
        return conn.execute(
            "SELECT * FROM competitors ORDER BY crawled_at DESC LIMIT ?", (limit,)
        ).fetchall()


# ── 중복 방지 ────────────────────────────────────────────

def get_recent_topics(days: int = 14, limit: int = 100) -> list[str]:
    """최근 N일간 발행된 주제 목록 반환 (중복 방지용)."""
    with _conn() as conn:
        rows = conn.execute(
            """SELECT DISTINCT topic FROM posts
               WHERE posted_at >= datetime('now', ?, 'localtime')
               ORDER BY posted_at DESC LIMIT ?""",
            (f"-{days} days", limit),
        ).fetchall()
    return [r["topic"] for r in rows]


def get_recent_article_urls(days: int = 7) -> set[str]:
    """최근 N일간 사용된 기사 URL 반환 (같은 기사 재생성 방지용)."""
    # image_dir 컬럼에 URL이 없어서 topic 기반으로 대신 사용
    # URL 저장이 필요하면 posts 테이블에 source_url 컬럼 추가 필요
    return set()  # 향후 확장 포인트


# ── 초기화 실행 ───────────────────────────────────────────
init_db()
