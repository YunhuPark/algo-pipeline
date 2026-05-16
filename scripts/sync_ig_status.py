"""
Instagram 게시물 상태 동기화 스크립트
- 로컬 DB의 ig_post_id를 Instagram API로 확인
- 삭제된 게시물은 DB status='deleted', meta.json에도 status 반영
- export_meta.py를 호출해 algo-site posts_meta.json 갱신
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src.db import get_posts, update_post_status


def sync_ig_status() -> None:
    from src.agents.publisher import check_post_exists

    posts = get_posts(limit=1000, include_deleted=False)
    ig_posts = [p for p in posts if p["post_id"] and p["platform"] == "instagram"]

    if not ig_posts:
        print("[Sync] 확인할 Instagram 게시물 없음")
        return

    print(f"[Sync] {len(ig_posts)}개 게시물 상태 확인 중...")
    deleted_count = 0

    for post in ig_posts:
        post_id = post["post_id"]
        exists = check_post_exists(post_id)
        if not exists:
            update_post_status(post_id, "deleted")
            # output 폴더의 meta.json에도 status 반영
            image_dir = post["image_dir"]
            if image_dir:
                meta_path = Path(image_dir) / "meta.json"
                if not meta_path.is_absolute():
                    meta_path = ROOT / "output" / image_dir / "meta.json"
                if meta_path.exists():
                    try:
                        data = json.loads(meta_path.read_text(encoding="utf-8"))
                        data["status"] = "deleted"
                        meta_path.write_text(
                            json.dumps(data, ensure_ascii=False, indent=2),
                            encoding="utf-8",
                        )
                    except Exception as e:
                        print(f"  ⚠️ meta.json 업데이트 실패 ({post_id}): {e}")
            deleted_count += 1
            print(f"  [삭제됨] {post['topic']} ({post_id})")
        else:
            print(f"  [활성] {post['topic'][:20]}")

    print(f"\n[Sync] 완료 — 활성 {len(ig_posts) - deleted_count}개 / 삭제 {deleted_count}개")

    # algo-site posts_meta.json 갱신
    try:
        import subprocess as _sp
        _exp = ROOT / "scripts" / "export_meta.py"
        _sp.run([sys.executable, str(_exp)], check=False)
    except Exception as e:
        print(f"  ⚠️ export_meta 실패: {e}")


if __name__ == "__main__":
    sync_ig_status()
