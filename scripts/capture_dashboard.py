"""
Flask 대시보드 스크린샷 자동 캡처 → algo-site/public/dashboard/
실행: python scripts/capture_dashboard.py

요구사항: pip install playwright && python -m playwright install chromium
Flask 서버(localhost:5001)가 실행 중이어야 합니다.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
ALGO_SITE_TARGETS = [
    ROOT / "algo-site" / "public" / "dashboard",
    ROOT.parent / "algo-site" / "public" / "dashboard",
]

PAGES = [
    ("main",      "http://localhost:5001/"),
    ("generate",  "http://localhost:5001/generate"),
    ("queue",     "http://localhost:5001/queue"),
    ("analytics", "http://localhost:5001/analytics"),
    ("settings",  "http://localhost:5001/settings"),
]

VIEWPORT = {"width": 1440, "height": 900}


def _find_output_dir() -> Path:
    for t in ALGO_SITE_TARGETS:
        if t.parent.parent.parent.exists():
            t.mkdir(parents=True, exist_ok=True)
            return t
    raise FileNotFoundError("algo-site/public/dashboard 경로를 찾을 수 없습니다.")


def capture(base_url: str = "http://localhost:5001") -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright가 설치되지 않았습니다: pip install playwright")
        sys.exit(1)

    out = _find_output_dir()

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport=VIEWPORT)

        for name, path in PAGES:
            url = path.replace("http://localhost:5001", base_url)
            print(f"  캡처 중: {url}")
            page.goto(url, wait_until="networkidle")
            page.wait_for_timeout(800)
            dest = out / f"{name}.png"
            page.screenshot(path=str(dest), full_page=False)
            print(f"  -> {dest}")

        browser.close()

    print(f"\n완료: {len(PAGES)}개 스크린샷 저장됨 → {out}")
    print("배포하려면: cd algo-site && vercel --prod")


if __name__ == "__main__":
    base = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:5001"
    capture(base)
