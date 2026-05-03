"""
기존 output 폴더에 meta.json이 없는 경우 script.json에서 백필합니다.
새로 추가된 파이프라인 실행 결과에만 완전한 메타데이터가 저장됩니다.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent
OUTPUT_DIR = ROOT / "output"

# 폴더명 패턴: 20260502_1244_토픽명
_TS_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})")


def _posted_at(folder_name: str) -> str:
    m = _TS_RE.match(folder_name)
    if m:
        y, mo, d, h, mi = m.groups()
        return f"{y}-{mo}-{d} {h}:{mi}:00"
    return ""


def backfill() -> None:
    filled = 0
    skipped = 0

    for folder in sorted(OUTPUT_DIR.iterdir()):
        if not folder.is_dir():
            continue
        meta_path = folder / "meta.json"
        if meta_path.exists():
            skipped += 1
            continue

        script_path = folder / "script.json"
        if not script_path.exists():
            continue

        try:
            script = json.loads(script_path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  ⚠️ {folder.name}/script.json 읽기 실패: {e}")
            continue

        meta = {
            "topic": script.get("topic", ""),
            "source_title": "",
            "source_url": "",
            "angle": "",
            "fact_confirmed": 0,
            "fact_disputed": 0,
            "fact_unverifiable": 0,
            "generation_seconds": 0,
            "ig_post_id": "",
            "permalink": "",
            "posted_at": _posted_at(folder.name),
            "folder": folder.name,
        }

        meta_path.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"  OK{folder.name}")
        filled += 1

    print(f"\n완료: {filled}개 백필, {skipped}개 이미 존재")


if __name__ == "__main__":
    backfill()
