"""
output/ 하위 폴더의 meta.json을 읽어
algo-site/src/data/posts_meta.json 으로 내보냅니다.
파이프라인 실행 후 자동 호출하거나 단독으로 실행할 수 있습니다.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
OUTPUT_DIR = ROOT / "output"

# cardnews 내부 경로 + 이전 Desktop 경로 + 독립 배포용 경로 모두 갱신
_TARGETS = [
    ROOT / "algo-site" / "src" / "data",
    ROOT.parent / "algo-site" / "src" / "data",
    Path(r"C:\Users\박윤후\Desktop\프로젝트\algo-site") / "src" / "data",
]


def export() -> int:
    records: list[dict] = []

    if not OUTPUT_DIR.exists():
        print("  -> output directory not found; metadata export skipped")
        return 0

    for folder in sorted(OUTPUT_DIR.iterdir(), reverse=True):
        meta_path = folder / "meta.json"
        if not meta_path.exists():
            continue
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
            # 삭제된 게시물은 algo-site에서 제외
            if data.get("status") == "deleted":
                continue
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

    # External repository writes are explicit opt-in.  Normal generation only
    # exports local metadata and never commits or pushes another repository.
    if os.environ.get("AUTO_EXPORT_META_GIT_PUSH", "false").lower() == "true":
        _git_push()

    return len(records)


def _git_push() -> None:
    import subprocess as _sp
    # git repo가 있는 algo-site 경로 찾기 (.git 폴더 기준)
    candidates = [t.parent.parent.parent for t in _TARGETS]
    algo_site = next((p for p in candidates if (p / ".git").exists()), None)
    if not algo_site:
        return
    # Python subprocess + cwd로 직접 호출 — CreateProcessW가 한글 경로 정상 처리
    try:
        target_file = next(
            (
                target / "posts_meta.json"
                for target in _TARGETS
                if algo_site in target.parents and (target / "posts_meta.json").exists()
            ),
            None,
        )
        if target_file is None:
            print("  -> algo-site metadata target 없음 (git 스킵)")
            return
        relative_target = target_file.relative_to(algo_site).as_posix()
        _sp.run(["git", "add", "--", relative_target], cwd=str(algo_site), check=True, capture_output=True)
        result = _sp.run(["git", "diff", "--cached", "--quiet"], cwd=str(algo_site), capture_output=True)
        if result.returncode == 0:
            print("  -> algo-site 변경 없음 (스킵)")
            return
        _sp.run(["git", "commit", "-m", "data: posts_meta.json 자동 갱신"], cwd=str(algo_site), check=True, capture_output=True)
        _sp.run(["git", "push", "origin", "HEAD"], cwd=str(algo_site), check=True, capture_output=True)
        print("  -> algo-site git push 완료 (Vercel 배포 트리거)")
    except Exception as e:
        print(f"  -> git push 실패 (무시): {e}")


if __name__ == "__main__":
    count = export()
    sys.exit(0 if count >= 0 else 1)
