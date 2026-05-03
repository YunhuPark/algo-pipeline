"""
Instagram Graph API에서 실제 게시물 목록을 가져와
ig_post_id가 비어있는 meta.json을 날짜 기준으로 업데이트합니다.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

import httpx

IG_ACCESS_TOKEN = os.getenv("IG_ACCESS_TOKEN", "")
IG_USER_ID = os.getenv("IG_USER_ID", "")
GRAPH_BASE = "https://graph.instagram.com/v21.0"
KST = timezone(timedelta(hours=9))


def fetch_all_ig_posts() -> list[dict]:
    """Instagram 계정의 전체 게시물 목록 (id, permalink, timestamp) 가져오기"""
    posts = []
    url = f"{GRAPH_BASE}/{IG_USER_ID}/media"
    params = {
        "fields": "id,permalink,timestamp",
        "access_token": IG_ACCESS_TOKEN,
        "limit": 50,
    }
    while url:
        resp = httpx.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        posts.extend(data.get("data", []))
        next_page = data.get("paging", {}).get("next")
        url = next_page if next_page else None
        params = {}  # next URL에 이미 파라미터 포함됨
    return posts


def main() -> None:
    if not IG_ACCESS_TOKEN or not IG_USER_ID:
        print("IG_ACCESS_TOKEN / IG_USER_ID가 .env에 없습니다.")
        sys.exit(1)

    print("Instagram 게시물 목록 가져오는 중...")
    ig_posts = fetch_all_ig_posts()
    print(f"  총 {len(ig_posts)}건 확인")

    # Instagram timestamp → KST 날짜 매핑
    # timestamp 예: "2026-04-23T12:34:56+0000"
    ig_by_date: dict[str, list[dict]] = {}
    for p in ig_posts:
        ts = datetime.fromisoformat(p["timestamp"].replace("+0000", "+00:00"))
        kst_date = ts.astimezone(KST).strftime("%Y-%m-%d")
        ig_by_date.setdefault(kst_date, []).append(p)

    # output 폴더의 meta.json 순회
    output_dir = ROOT / "output"
    updated = 0
    skipped = 0

    for folder in sorted(output_dir.iterdir(), reverse=True):
        meta_path = folder / "meta.json"
        if not meta_path.exists():
            continue

        meta = json.loads(meta_path.read_text(encoding="utf-8"))

        if meta.get("ig_post_id"):  # 이미 있으면 스킵
            skipped += 1
            continue

        posted_at = meta.get("posted_at", "")
        if not posted_at:
            continue

        post_date = posted_at[:10]  # "2026-04-23"
        candidates = ig_by_date.get(post_date, [])

        if not candidates:
            print(f"  [{post_date}] {folder.name[:30]} - 해당 날짜 IG 게시물 없음")
            continue

        if len(candidates) == 1:
            match = candidates[0]
        else:
            # 같은 날 여러 건이면 폴더 시간과 가장 가까운 것 선택
            posted_time = datetime.strptime(posted_at, "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST)
            match = min(
                candidates,
                key=lambda p: abs(
                    datetime.fromisoformat(p["timestamp"].replace("+0000", "+00:00"))
                    .astimezone(KST)
                    - posted_time
                ),
            )

        meta["ig_post_id"] = match["id"]
        meta["permalink"] = match["permalink"]
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  [{post_date}] {folder.name[:30]} → {match['permalink']}")
        updated += 1

    print(f"\n완료: {updated}건 업데이트 / {skipped}건 스킵 (이미 있음)")

    if updated > 0:
        print("\nposts_meta.json 재내보내기 중...")
        from scripts.export_meta import export
        export()


if __name__ == "__main__":
    main()
