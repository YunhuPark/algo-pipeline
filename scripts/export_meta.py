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

# cardnews 내부 경로 + 이전 Desktop 경로 + 독립 배포용 경로 모두 갱신
_TARGETS = [
    ROOT / "algo-site" / "src" / "data",
    ROOT.parent / "algo-site" / "src" / "data",
    Path(r"C:\Users\박윤후\Desktop\프로젝트\algo-site") / "src" / "data",
]


def export() -> int:
    records: list[dict] = []

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

    # algo-site git push → Vercel 자동 배포
    _git_push()

    return len(records)


def _git_push() -> None:
    import subprocess as _sp
    import os
    # git repo가 있는 algo-site 경로 찾기 (.git 폴더 기준)
    candidates = [t.parent.parent.parent for t in _TARGETS]
    algo_site = next((p for p in candidates if (p / ".git").exists()), None)
    if not algo_site:
        return
    # PowerShell로 실행 → 한글 경로도 안전하게 처리
    ps_script = (
        f"Set-Location '{algo_site}'; "
        f"git add src/data/posts_meta.json; "
        f"$diff = git diff --cached --quiet; "
        f"if ($LASTEXITCODE -ne 0) {{"
        f"  git commit -m 'data: posts_meta.json 자동 갱신'; "
        f"  git push origin main; "
        f"  Write-Host 'algo-site git push 완료'"
        f"}}"
    )
    try:
        result = _sp.run(
            ["powershell", "-NonInteractive", "-NoProfile", "-Command", ps_script],
            capture_output=True, text=True, encoding="utf-8", errors="replace"
        )
        if "git push 완료" in result.stdout:
            print("  -> algo-site git push 완료 (Vercel 배포 트리거)")
        else:
            print("  -> algo-site 변경 없음 (스킵)")
    except Exception as e:
        print(f"  -> git push 실패 (무시): {e}")


if __name__ == "__main__":
    count = export()
    sys.exit(0 if count >= 0 else 1)
