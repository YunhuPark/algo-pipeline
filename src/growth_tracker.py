"""Daily growth record: follower counts and per-post reach/engagement for Instagram and Threads.

Read-only: it only reads from the platform APIs and writes to data/growth.db (one row per day, so
running it twice in a day updates the day instead of duplicating it). Credentials come from this
project's .env only, and the Threads token is checked to belong to the algo account first (another
project keeps its own Threads token in the Windows environment).
"""
from __future__ import annotations

import sqlite3
import threading
from datetime import date, datetime
from pathlib import Path
from typing import Callable

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "growth.db"
ENV_PATH = ROOT / ".env"
IG_BASE = "https://graph.instagram.com/v21.0"
TH_BASE = "https://graph.threads.net/v1.0"
RECENT_POSTS = 12          # how many of the latest posts per platform are re-measured each day

IG_METRICS = ("reach", "views", "saved", "shares")
TH_METRICS = ("views", "likes", "replies", "reposts", "quotes")

SCHEMA = """
create table if not exists account_snapshots (
    day text not null, platform text not null, followers integer, media_count integer, captured_at text not null,
    primary key (day, platform)
);
create table if not exists post_snapshots (
    day text not null, platform text not null, post_id text not null, posted_at text, permalink text,
    reach integer, views integer, likes integer, comments integer, saves integer, shares integer,
    replies integer, reposts integer, quotes integer, captured_at text not null,
    primary key (day, platform, post_id)
);
"""

Getter = Callable[[str, dict], dict]


def _http_get(url: str, params: dict) -> dict:
    try:
        data = httpx.get(url, params=params, timeout=20).json()
    except (httpx.HTTPError, ValueError):
        return {"error": {"message": "request failed"}}
    return data if isinstance(data, dict) else {"error": {"message": "unexpected response"}}


def _metric_values(payload: dict) -> dict[str, int]:
    values: dict[str, int] = {}
    for item in payload.get("data", []) or []:
        try:
            raw = item["values"][0]["value"] if item.get("values") else item["total_value"]["value"]
            values[item["name"]] = int(raw)
        except (KeyError, IndexError, TypeError, ValueError):
            continue
    return values


def collect(day: date, ig_token: str, th_token: str, th_user_id: str, get: Getter = _http_get) -> dict:
    """Read today's numbers. A metric that cannot be read stays None; it never aborts the run."""
    out: dict = {"accounts": [], "posts": [], "notes": []}

    if ig_token:
        me = get(f"{IG_BASE}/me", {"fields": "followers_count,media_count", "access_token": ig_token})
        if "error" in me:
            out["notes"].append("Instagram 계정 정보를 읽지 못했습니다")
        else:
            out["accounts"].append(("instagram", me.get("followers_count"), me.get("media_count")))
        media = get(f"{IG_BASE}/me/media", {"fields": "id,timestamp,permalink,like_count,comments_count", "limit": RECENT_POSTS, "access_token": ig_token})
        for item in media.get("data", []) or []:
            metrics = {}
            for name in IG_METRICS:
                metrics.update(_metric_values(get(f"{IG_BASE}/{item['id']}/insights", {"metric": name, "access_token": ig_token})))
            out["posts"].append({
                "platform": "instagram", "post_id": item["id"], "posted_at": item.get("timestamp"),
                "permalink": item.get("permalink"), "reach": metrics.get("reach"), "views": metrics.get("views"),
                "likes": item.get("like_count"), "comments": item.get("comments_count"),
                "saves": metrics.get("saved"), "shares": metrics.get("shares"),
            })
    else:
        out["notes"].append("IG_ACCESS_TOKEN이 .env에 없습니다")

    if th_token and th_user_id:
        followers = _metric_values(get(f"{TH_BASE}/me/threads_insights", {"metric": "followers_count", "access_token": th_token}))
        out["accounts"].append(("threads", followers.get("followers_count"), None))
        posts = get(f"{TH_BASE}/me/threads", {"fields": "id,timestamp,permalink", "limit": RECENT_POSTS, "access_token": th_token})
        for item in posts.get("data", []) or []:
            metrics = _metric_values(get(f"{TH_BASE}/{item['id']}/insights", {"metric": ",".join(TH_METRICS), "access_token": th_token}))
            out["posts"].append({
                "platform": "threads", "post_id": item["id"], "posted_at": item.get("timestamp"),
                "permalink": item.get("permalink"), "views": metrics.get("views"), "likes": metrics.get("likes"),
                "replies": metrics.get("replies"), "reposts": metrics.get("reposts"), "quotes": metrics.get("quotes"),
            })
    else:
        out["notes"].append("Threads 자격증명이 .env에 없습니다")
    return out


def save(conn: sqlite3.Connection, day: date, data: dict) -> None:
    conn.executescript(SCHEMA)
    now, key = datetime.now().isoformat(timespec="seconds"), day.isoformat()
    for platform, followers, media_count in data["accounts"]:
        conn.execute(
            "insert or replace into account_snapshots(day, platform, followers, media_count, captured_at) values (?,?,?,?,?)",
            (key, platform, followers, media_count, now),
        )
    columns = ("reach", "views", "likes", "comments", "saves", "shares", "replies", "reposts", "quotes")
    for post in data["posts"]:
        conn.execute(
            "insert or replace into post_snapshots(day, platform, post_id, posted_at, permalink, "
            + ", ".join(columns) + ", captured_at) values (?,?,?,?,?," + ",".join("?" * len(columns)) + ",?)",
            (key, post["platform"], post["post_id"], post.get("posted_at"), post.get("permalink"),
             *[post.get(c) for c in columns], now),
        )
    conn.commit()


def report(conn: sqlite3.Connection, posts_shown: int = 6) -> str:
    """A short text report of the follower trend and the latest numbers of the newest posts."""
    conn.executescript(SCHEMA)
    lines: list[str] = []
    days = [r[0] for r in conn.execute("select distinct day from account_snapshots order by day")]
    if not days:
        return "성장 기록이 아직 없습니다. python scripts/growth_snapshot.py 로 첫 기록을 남기세요."
    lines.append(f"성장 기록 {days[0]} ~ {days[-1]} (기록한 날 {len(days)}일)")
    for platform in ("instagram", "threads"):
        rows = conn.execute(
            "select day, followers from account_snapshots where platform=? and followers is not null order by day", (platform,)
        ).fetchall()
        if rows:
            first, last = rows[0][1], rows[-1][1]
            lines.append(f"- 팔로워 {platform}: {first} → {last} ({last - first:+d})")
    latest = days[-1]
    for platform in ("instagram", "threads"):
        rows = conn.execute(
            "select posted_at, reach, views, likes, comments, saves, shares, replies, reposts from post_snapshots "
            "where day=? and platform=? order by posted_at desc limit ?", (latest, platform, posts_shown),
        ).fetchall()
        if not rows:
            continue
        lines.append(f"- {platform} 최근 게시물 ({latest} 기준)")
        for posted, reach, views, likes, comments, saves, shares, replies, reposts in rows:
            parts = [("도달", reach), ("조회", views), ("좋아요", likes), ("댓글", comments), ("저장", saves),
                     ("공유", shares), ("답글", replies), ("리포스트", reposts)]
            shown = " ".join(f"{name} {value}" for name, value in parts if value is not None)
            lines.append(f"    {str(posted)[:10]}  {shown or '수치 없음'}")
    return "\n".join(lines)


def snapshot(day: date | None = None, db_path: Path = DB_PATH, get: Getter = _http_get) -> dict:
    """Collect and store today's numbers; returns what was collected (notes explain what was skipped)."""
    from src import threads_credentials

    day = day or date.today()
    env = dotenv_values(ENV_PATH) if ENV_PATH.exists() else {}
    ig_token = (env.get("IG_ACCESS_TOKEN") or "").strip()
    th_token, th_user = threads_credentials.load()
    notes: list[str] = []
    if th_token and th_user:
        try:
            threads_credentials.assert_expected_account(th_token, th_user)
        except ValueError as exc:                       # never read another project's account by mistake
            notes.append(str(exc))
            th_token = th_user = ""
    data = collect(day, ig_token, th_token, th_user, get)
    data["notes"] = notes + data["notes"]
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        save(conn, day, data)
    return data


def has_today(db_path: Path = DB_PATH, day: date | None = None) -> bool:
    if not db_path.exists():
        return False
    try:
        with sqlite3.connect(db_path) as conn:
            conn.executescript(SCHEMA)
            return bool(conn.execute("select 1 from account_snapshots where day=? limit 1", ((day or date.today()).isoformat(),)).fetchone())
    except sqlite3.Error:
        return False


_started = False


def ensure_today_async() -> None:
    """Take today's snapshot in the background once per day (not a scheduled task: it runs when the
    dashboard is opened). Silent on any failure."""
    global _started
    if _started or has_today():
        return
    _started = True
    threading.Thread(target=lambda: _quiet_snapshot(), daemon=True).start()


def _quiet_snapshot() -> None:
    try:
        snapshot()
    except Exception:
        pass
