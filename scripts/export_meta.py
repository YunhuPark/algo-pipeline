"""
output/ 하위 폴더의 meta.json을 읽어
algo-site/src/data/posts_meta.json 으로 내보냅니다.
파이프라인 실행 후 자동 호출하거나 단독으로 실행할 수 있습니다.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
OUTPUT_DIR = ROOT / "output"

# cardnews 내부 경로와 독립 배포용 경로 둘 다 갱신
_TARGETS = [
    ROOT / "algo-site" / "src" / "data",
    ROOT.parent / "algo-site" / "src" / "data",
]


def export() -> int:
    records: list[dict] = []

    for folder in sorted(OUTPUT_DIR.iterdir(), reverse=True):
        meta_path = folder / "meta.json"
        if not meta_path.exists():
            continue
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
            data["folder"] = folder.name
            records.append(data)
        except Exception as e:
            print(f"  posts_meta warning: {folder.name}: {e}")

    payload = json.dumps(records, ensure_ascii=False, indent=2)
    saved = 0
    for target in _TARGETS:
        if target.parent.parent.parent.exists():
            target.mkdir(parents=True, exist_ok=True)
            (target / "posts_meta.json").write_text(payload, encoding="utf-8")
            saved += 1

    print(f"  -> posts_meta.json saved ({len(records)} records, {saved} targets)")

    # algo-site git push → Vercel 자동 배포
    _git_push()

    return len(records)


def _git_push() -> None:
    import subprocess as _sp
    # git repo가 있는 algo-site 경로 찾기 (.git 폴더 기준)
    candidates = [t.parent.parent.parent for t in _TARGETS]
    algo_site = next((p for p in candidates if (p / ".git").exists()), None)
    if not algo_site:
        return
    try:
        _sp.run(["git", "add", "src/data/posts_meta.json"], cwd=str(algo_site), check=True, capture_output=True)
        result = _sp.run(["git", "diff", "--cached", "--quiet"], cwd=str(algo_site), capture_output=True)
        if result.returncode == 0:
            return  # 변경 없으면 스킵
        _sp.run(["git", "commit", "-m", "data: posts_meta.json 자동 갱신"], cwd=str(algo_site), check=True, capture_output=True)
        _sp.run(["git", "push", "origin", "main"], cwd=str(algo_site), check=True, capture_output=True)
        print("  -> algo-site git push 완료 (Vercel 배포 트리거)")
    except Exception as e:
        print(f"  -> git push 실패 (무시): {e}")


if __name__ == "__main__":
    count = export()
    sys.exit(0 if count >= 0 else 1)
