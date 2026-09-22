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
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from src.db_factory import get_connection
from src.schemas.queue_schemas import CollectionMethod, QueueMetadataV2


def resolve_algo_db_path(settings=None) -> Path:
    if settings and hasattr(settings, "ALGO_DB_PATH"):
        return Path(settings.ALGO_DB_PATH)
    env_path = os.environ.get("ALGO_DB_PATH")
    algo_env = os.environ.get("ALGO_ENV", "production").lower()
    if algo_env in ("test", "synthetic"):
        if not env_path:
            raise RuntimeError(f"[{algo_env}] ALGO_DB_PATH must be explicitly injected via env or settings.")
        return Path(env_path)
    return Path(env_path) if env_path else Path("data/algo.db")

def init_db(db_path: Path | None = None) -> None:
    """DB 및 테이블 초기화 (최초 1회)."""
    target_path = db_path or resolve_algo_db_path()
    target_path.parent.mkdir(parents=True, exist_ok=True)
    with _conn(target_path) as conn:
        # 기존 DB 마이그레이션: status 컬럼 없으면 추가
        try:
            conn.execute("ALTER TABLE posts ADD COLUMN status TEXT DEFAULT 'active'")
        except Exception:
            pass  # 이미 존재하면 무시
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
            status      TEXT DEFAULT 'active',  -- active / deleted
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
def _conn(db_path: Path | None = None):
    target_path = db_path or resolve_algo_db_path()
    target_path.parent.mkdir(parents=True, exist_ok=True)
    conn = get_connection(target_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
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


def get_posts(platform: str | None = None, limit: int = 50, include_deleted: bool = False) -> list[sqlite3.Row]:
    status_filter = "" if include_deleted else "AND (status IS NULL OR status != 'deleted')"
    with _conn() as conn:
        if platform:
            return conn.execute(
                f"SELECT * FROM posts WHERE platform=? {status_filter} ORDER BY posted_at DESC LIMIT ?",
                (platform, limit),
            ).fetchall()
        return conn.execute(
            f"SELECT * FROM posts WHERE 1=1 {status_filter} ORDER BY posted_at DESC LIMIT ?", (limit,)
        ).fetchall()


def update_post_status(post_id: str, status: str) -> None:
    with _conn() as conn:
        conn.execute("UPDATE posts SET status=? WHERE post_id=?", (status, post_id))


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


def enqueue_v2(
    metadata: QueueMetadataV2,
    collection_method: CollectionMethod,
    *,
    angle_hint: str = "",
    scheduled_at: str | None = None,
) -> int:
    """Insert an attested queue row. The Queue V2 migration must already be applied."""
    if collection_method in {
        CollectionMethod.LEGACY_UNVERIFIED,
        CollectionMethod.TEST_FIXTURE,
        CollectionMethod.SYNTHETIC,
    } and os.environ.get("ALGO_ENV", "production").lower() == "production":
        raise ValueError(f"{collection_method.value} cannot be enqueued in production")

    with _conn() as conn:
        cur = conn.execute(
            """INSERT INTO queue (
                   topic, context, angle_hint, metadata_json, collection_method,
                   lineage_hash, metadata_schema_version, scheduled_at,
                   publish_attempt_state
               ) VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                metadata.topic,
                metadata.context,
                angle_hint,
                metadata.canonical_json(),
                collection_method.value,
                metadata.lineage_hash(),
                metadata.schema_version,
                scheduled_at,
                "NOT_ATTEMPTED",
            ),
        )
        return int(cur.lastrowid)


def dequeue_next() -> sqlite3.Row | None:
    """다음 발행 대기 항목 (scheduled_at 기준, NULL이면 우선).

    'ready'(이미 생성만 해둔 항목)도 포함한다 — 안 그러면 사람이 미리보기를
    승인한 뒤 실제 발행을 시도할 방법이 없어져 그 항목이 영원히 멈춰버린다.
    """
    with _conn() as conn:
        row = conn.execute(
            """SELECT * FROM queue
               WHERE status IN ('pending', 'ready')
                 AND (
                       publish_error_code IS NULL OR publish_error_code IN (
                           'NETWORK_TIMEOUT_BEFORE_PUBLISH',
                           'RATE_LIMITED_BEFORE_PUBLISH',
                           'TEMPORARY_UPSTREAM_UNAVAILABLE',
                           'PIPELINE_PRE_PUBLISH_TRANSIENT'
                       )
                 )
                 AND ig_post_id IS NULL
                 AND publish_attempt_id IS NULL
                 AND publish_attempt_state = 'NOT_ATTEMPTED'
                 AND retry_count < 3
                 AND (scheduled_at IS NULL OR scheduled_at <= datetime('now','localtime'))
               ORDER BY scheduled_at ASC NULLS FIRST, id ASC
               LIMIT 1""",
        ).fetchone()
        return row


def mark_queue_status(queue_id: int, status: str) -> None:
    with _conn() as conn:
        conn.execute("UPDATE queue SET status=? WHERE id=?", (status, queue_id))


def claim_queue_row(queue_id: int) -> bool:
    """Atomically move a pending/ready row into a transient 'processing' state.

    dequeue_next() is a plain SELECT with no locking of its own; two
    concurrent callers (a scheduler cron and a dashboard click racing at the
    same moment) can otherwise both select the same row and both start
    generating/reading its render. claim_queue_row() is the actual mutex:
    dequeue_next()'s WHERE clause already excludes 'processing' rows, so once
    one caller wins this UPDATE, every other caller's dequeue_next() call
    stops returning this row until it's unclaimed.
    """
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE queue SET status='processing' WHERE id=? AND status IN ('pending','ready')",
            (queue_id,),
        )
        return cur.rowcount == 1


def unclaim_queue_row(queue_id: int, revert_to: str) -> None:
    """Return a claimed row to a real status once this attempt is finished.

    Safe to call unconditionally from a `finally` block: the WHERE guard
    makes it a no-op if something else already resolved the row's status in
    the meantime (mark_queue_status/complete_queue_publish already moved it
    to 'ready'/'published'/'skipped').
    """
    with _conn() as conn:
        conn.execute(
            "UPDATE queue SET status=? WHERE id=? AND status='processing'",
            (revert_to, queue_id),
        )


def recover_stuck_processing_rows() -> int:
    """Reset any row still stuck in the transient 'processing' claim state.

    'processing' only ever means "some in-memory Python thread is actively
    working on this row right now." That in-memory state can't survive a
    process restart (the dev server's auto-reloader restarting because a
    file changed mid-generation, a crash, a manual kill) - the thread that
    would have called unclaim_queue_row() in its `finally` block is simply
    gone, and nothing else will ever release that claim. Call this once at
    process startup, when no thread in *this* process could legitimately
    still be processing anything left over from a previous process.
    Reverts to 'ready' when a render is already cached, 'pending' otherwise.
    Returns the number of rows recovered.
    """
    with _conn() as conn:
        cur = conn.execute(
            """UPDATE queue
               SET status = CASE WHEN image_dir != '' THEN 'ready' ELSE 'pending' END,
                   publish_attempt_id=NULL, publish_started_at=NULL,
                   publish_attempt_state='NOT_ATTEMPTED'
               WHERE status='processing'"""
        )
        return cur.rowcount


def set_queue_image_dir(queue_id: int, image_dir: str) -> None:
    """Persist where a generation-only run rendered this row's cards.

    Without this, a row that reaches 'ready' status has nothing recorded
    telling a later publish attempt which already-approved render to
    actually upload, forcing (or silently skipping) a full regeneration
    that could produce different cards than the ones a human reviewed.
    """
    with _conn() as conn:
        conn.execute(
            "UPDATE queue SET image_dir=? WHERE id=?", (image_dir, queue_id)
        )


def try_mark_queue_skipped(queue_id: int) -> bool:
    """Skip a queue row only while it is still safely skippable.

    dequeue_next()/publish_next() run in a separate long-lived process (a
    scheduler cron, a queue worker) that may already have claimed this row
    via start_publish_attempt() and be mid-publish when a user asks to skip
    it. A plain unconditional UPDATE can't tell "nothing is happening yet"
    from "the real Instagram publish is already in flight" — and once IG's
    API call succeeds there is nothing a DB write can undo anyway. This only
    marks the row skipped while it is still 'pending' and unclaimed, so a
    later complete_queue_publish() (guarded the same way) can't silently
    resurrect it back to 'published' and hide that the skip was ignored.
    Returns True if the skip took effect, False if the row was already
    claimed/in-flight (or gone) and the caller should tell the user that.
    """
    with _conn() as conn:
        cur = conn.execute(
            """UPDATE queue SET status='skipped'
               WHERE id=? AND status IN ('pending', 'ready')
                 AND publish_attempt_id IS NULL
                 AND publish_attempt_state='NOT_ATTEMPTED'""",
            (queue_id,),
        )
        return cur.rowcount == 1


def clear_queue_error(queue_id: int) -> bool:
    """Manually clear a permanent publish error so the row becomes retryable.

    This exists for a human to trigger from the dashboard after they've
    actually checked whether anything already went out — most of the error
    codes it clears (REMOTE_PUBLISH_PERSISTENCE_UNCERTAIN,
    UNCERTAIN_EMPTY_POST_ID, ...) exist specifically because we could not
    tell whether an Instagram post already succeeded, so retrying without
    checking risks a duplicate post. Refuses a row that is already
    published, skipped, or currently claimed/mid-attempt (status='processing'
    or ig_post_id already set). Reverts to 'ready' (not 'pending') when a
    cached render is still on disk, so retrying re-uses it instead of
    needlessly regenerating.
    """
    with _conn() as conn:
        row = conn.execute(
            "SELECT status, image_dir, ig_post_id FROM queue WHERE id=?",
            (queue_id,),
        ).fetchone()
        if row is None or row["ig_post_id"] or row["status"] in (
            "published", "skipped", "processing",
        ):
            return False
        revert_status = "ready" if row["image_dir"] else "pending"
        cur = conn.execute(
            """UPDATE queue
               SET status=?, publish_error_code=NULL,
                   publish_attempt_id=NULL, publish_started_at=NULL,
                   publish_attempt_state='NOT_ATTEMPTED'
               WHERE id=? AND status NOT IN ('published', 'skipped', 'processing')
                 AND ig_post_id IS NULL""",
            (revert_status, queue_id),
        )
        return cur.rowcount == 1


def mark_queue_error(
    queue_id: int,
    error_code: str,
    *,
    increment_retry: bool = False,
    preserve_attempt: bool = True,
) -> None:
    assignments = ["publish_error_code=?"]
    params: list[object] = [error_code]
    if increment_retry:
        assignments.append("retry_count=retry_count+1")
    if not preserve_attempt:
        assignments.extend(
            [
                "publish_attempt_id=NULL",
                "publish_started_at=NULL",
                "publish_attempt_state='NOT_ATTEMPTED'",
            ]
        )
    params.append(queue_id)
    with _conn() as conn:
        conn.execute(
            f"UPDATE queue SET {', '.join(assignments)} WHERE id=?",
            tuple(params),
        )


def start_publish_attempt(queue_id: int, attempt_id: str, started_at: str) -> None:
    with _conn() as conn:
        cur = conn.execute(
            """UPDATE queue
               SET publish_attempt_id=?, publish_started_at=?,
                   publish_attempt_state='STARTED', publish_error_code='PUBLISH_IN_PROGRESS'
               WHERE id=? AND status IN ('pending', 'ready', 'processing') AND ig_post_id IS NULL
                 AND publish_attempt_id IS NULL
                 AND publish_attempt_state='NOT_ATTEMPTED'""",
            (attempt_id, started_at, queue_id),
        )
        if cur.rowcount != 1:
            raise RuntimeError("publish attempt precondition failed")


def store_queue_ig_post_id(queue_id: int, attempt_id: str, ig_post_id: str) -> None:
    if not ig_post_id.strip():
        raise ValueError("ig_post_id must not be blank")
    with _conn() as conn:
        existing = conn.execute(
            "SELECT ig_post_id, publish_attempt_id FROM queue WHERE id=?", (queue_id,)
        ).fetchone()
        if existing is None or existing["publish_attempt_id"] != attempt_id:
            raise RuntimeError("publish attempt mismatch")
        if existing["ig_post_id"] not in (None, ig_post_id):
            raise RuntimeError("refusing to overwrite a different ig_post_id")
        conn.execute(
            """UPDATE queue SET ig_post_id=?, publish_attempt_state='REMOTE_ID_CONFIRMED'
               WHERE id=? AND publish_attempt_id=?""",
            (ig_post_id, queue_id, attempt_id),
        )


def complete_queue_publish(queue_id: int, attempt_id: str, ig_post_id: str) -> None:
    """Record that a claimed queue row's Instagram publish succeeded.

    ``status != 'skipped'`` guards against a race where a user skipped this
    row (via try_mark_queue_skipped) while the real IG publish call — already
    in flight and unstoppable by this point — was completing. It can't undo
    that publish, but it stops this write from silently overwriting the
    user's skip back to 'published' and hiding that the skip had no effect;
    the caller's exception handling records an error instead.
    """
    with _conn() as conn:
        cur = conn.execute(
            """UPDATE queue SET status='published', publish_error_code=NULL,
                   publish_attempt_state='REMOTE_ID_CONFIRMED'
               WHERE id=? AND publish_attempt_id=? AND ig_post_id=? AND status != 'skipped'""",
            (queue_id, attempt_id, ig_post_id),
        )
        if cur.rowcount != 1:
            raise RuntimeError("publish completion precondition failed")


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
# init_db() was removed to prevent module-level side effects.
