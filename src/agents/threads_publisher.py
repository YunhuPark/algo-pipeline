"""
Phase 6 (선택): Meta Threads API Publisher
생성된 카드뉴스 이미지를 Threads 캐러셀로 자동 업로드.

── Threads API 엔드포인트 ─────────────────────────────────
Base: https://graph.threads.net/v1.0/

1. 이미지 컨테이너 생성
   POST /{user-id}/threads
   params: media_type=IMAGE, image_url=<공개URL>, text=<캡션>

2. 캐러셀 아이템 컨테이너 생성 (이미지별)
   POST /{user-id}/threads
   params: media_type=IMAGE, image_url=<URL>, is_carousel_item=true

3. 캐러셀 컨테이너 생성
   POST /{user-id}/threads
   params: media_type=CAROUSEL, children=[id,...], text=<캡션>

4. 게시
   POST /{user-id}/threads_publish
   params: creation_id=<carousel_id>

── 사전 요구사항 ──────────────────────────────────────────
.env에 아래 항목 추가:
  THREADS_ACCESS_TOKEN=...   (없으면 IG_ACCESS_TOKEN 폴백)
  THREADS_USER_ID=...        (없으면 IG_USER_ID 폴백)

이미지 공개 URL:
  - ngrok 자동 터널 (로컬 개발)
  - IG_IMAGE_BASE_URL 환경변수 재활용 가능
──────────────────────────────────────────────────────────
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

# .env 로드 (config.py가 이미 로드했더라도 무해하게 재호출)
load_dotenv(override=False)

# ── 환경 변수 ─────────────────────────────────────────────
# Threads 전용 토큰 → 없으면 Instagram 토큰 폴백 (같은 Meta 플랫폼)
THREADS_ACCESS_TOKEN: str = (
    os.getenv("THREADS_ACCESS_TOKEN")
    or os.getenv("IG_ACCESS_TOKEN", "")
)
THREADS_USER_ID: str = (
    os.getenv("THREADS_USER_ID")
    or os.getenv("IG_USER_ID", "")
)
# 이미지 공개 URL 베이스 (ngrok 없이 정적 서버 사용 시)
_IMAGE_BASE_URL: str = os.getenv("IG_IMAGE_BASE_URL", "")

THREADS_API_BASE = "https://graph.threads.net/v1.0"


# ── ngrok 자동 터널 (publisher.py 로직 재활용) ───────────

def _get_public_url(image_dir: Path, port: int = 8765) -> str:
    """
    로컬 HTTP 서버 + ngrok 터널로 공개 URL 반환.
    publisher.py의 _get_public_url과 동일한 방식.
    output/ 루트를 서빙하고 하위 폴더 경로를 URL에 포함.
    """
    # 이미 publisher.py가 서버를 시작했을 수 있으므로 import로 재활용
    try:
        from src.agents.publisher import _get_public_url as _ig_get_public_url  # noqa: PLC0415
        return _ig_get_public_url(image_dir, port)
    except Exception:
        # 직접 시작 (publisher.py 없는 환경 대비)
        import threading
        from http.server import HTTPServer, SimpleHTTPRequestHandler

        class _QuietHandler(SimpleHTTPRequestHandler):
            def log_message(self, *_): pass

        output_root = image_dir.parent
        os.chdir(output_root)
        server = HTTPServer(("", port), _QuietHandler)
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        print(f"  [ThreadsPublisher] 로컬 서버 시작: http://localhost:{port}")

        import subprocess
        import sys
        try:
            import pyngrok  # noqa: F401
        except ImportError:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "pyngrok", "-q"])

        from pyngrok import ngrok  # noqa: PLC0415
        existing = ngrok.get_tunnels()
        for tunnel in existing:
            if str(port) in tunnel.config.get("addr", ""):
                pub = tunnel.public_url.replace("http://", "https://")
                return f"{pub}/{image_dir.name}"

        tunnel = ngrok.connect(port, "http")
        pub = tunnel.public_url.replace("http://", "https://")
        print(f"  [ThreadsPublisher] ngrok 터널 시작: {pub}")
        return f"{pub}/{image_dir.name}"


# ── Threads Graph API 헬퍼 ────────────────────────────────

def _create_carousel_item(image_url: str) -> str:
    """
    캐러셀 아이템용 이미지 컨테이너 생성 → container_id 반환.
    """
    resp = httpx.post(
        f"{THREADS_API_BASE}/{THREADS_USER_ID}/threads",
        params={
            "media_type":       "IMAGE",
            "image_url":        image_url,
            "is_carousel_item": "true",
            "access_token":     THREADS_ACCESS_TOKEN,
        },
        timeout=30,
    )
    data = resp.json()
    if "id" not in data:
        raise RuntimeError(f"Threads 이미지 컨테이너 생성 실패: {data}")
    return data["id"]


def _create_carousel_container(container_ids: list[str], caption: str) -> str:
    """
    캐러셀 컨테이너 생성 → carousel_id 반환.
    """
    resp = httpx.post(
        f"{THREADS_API_BASE}/{THREADS_USER_ID}/threads",
        params={
            "media_type":   "CAROUSEL",
            "children":     ",".join(container_ids),
            "text":         caption,
            "access_token": THREADS_ACCESS_TOKEN,
        },
        timeout=30,
    )
    data = resp.json()
    if "id" not in data:
        raise RuntimeError(f"Threads 캐러셀 컨테이너 생성 실패: {data}")
    return data["id"]


def _publish_container(creation_id: str) -> str:
    """
    생성된 컨테이너를 Threads에 게시 → post_id 반환.
    """
    resp = httpx.post(
        f"{THREADS_API_BASE}/{THREADS_USER_ID}/threads_publish",
        params={
            "creation_id":  creation_id,
            "access_token": THREADS_ACCESS_TOKEN,
        },
        timeout=30,
    )
    data = resp.json()
    if "id" not in data:
        raise RuntimeError(f"Threads 게시 실패: {data}")
    return data["id"]


def _wait_for_ready(container_id: str, max_wait: int = 60) -> None:
    """
    컨테이너 상태가 PUBLISHED/FINISHED 될 때까지 폴링 대기.
    Threads API는 status 필드로 상태를 반환.
    """
    for _ in range(max_wait // 3):
        time.sleep(3)
        resp = httpx.get(
            f"{THREADS_API_BASE}/{container_id}",
            params={
                "fields":       "status",
                "access_token": THREADS_ACCESS_TOKEN,
            },
            timeout=10,
        )
        status = resp.json().get("status", "").upper()
        if status in ("FINISHED", "PUBLISHED"):
            return
        if status == "ERROR":
            raise RuntimeError(f"Threads 미디어 처리 실패 (container: {container_id})")
    raise TimeoutError(f"Threads 미디어 처리 타임아웃 (container: {container_id})")


def _build_caption(hook: str, hashtags: list[str]) -> str:
    """Threads 캡션 조합 — Instagram publisher와 동일한 형식"""
    tag_str = " ".join(hashtags[:10])
    return f"{hook}\n.\n.\n.\n{tag_str}"


# ── 공개 인터페이스 ───────────────────────────────────────

def publish(
    image_paths: list[Path],
    hook: str,
    hashtags: list[str],
    base_url: str = "",
) -> str:
    """
    카드뉴스 이미지들을 Threads 캐러셀로 게시.

    Instagram publisher와 동일한 인터페이스.

    Args:
        image_paths: PNG 파일 경로 목록
        hook:        캡션 첫 줄 후킹 문구
        hashtags:    해시태그 목록 (최대 10개 사용)
        base_url:    이미지 공개 URL 베이스
                     (비어있으면 .env IG_IMAGE_BASE_URL → ngrok 자동 시작 순서로 처리)
    Returns:
        게시된 post_id
    """
    if not THREADS_ACCESS_TOKEN or not THREADS_USER_ID:
        raise EnvironmentError(
            ".env에 THREADS_ACCESS_TOKEN(또는 IG_ACCESS_TOKEN)과 "
            "THREADS_USER_ID(또는 IG_USER_ID)가 없습니다."
        )

    # PNG 파일만 필터
    imgs = [p for p in image_paths if p.suffix.lower() == ".png"]
    if not imgs:
        raise ValueError("업로드할 PNG 파일이 없습니다.")

    # 단일 이미지는 캐러셀 불필요 → 단순 IMAGE 게시
    if len(imgs) == 1:
        return _publish_single(imgs[0], hook, hashtags, base_url)

    # 공개 URL 결정: 명시 인자 → .env → ngrok 자동 시작
    url_base = (base_url or _IMAGE_BASE_URL or "").rstrip("/")
    if not url_base:
        print("  [ThreadsPublisher] 공개 URL 없음 → ngrok 자동 터널 시작...")
        image_dir = imgs[0].parent
        url_base = _get_public_url(image_dir).rstrip("/")

    print(f"\n  [ThreadsPublisher] Threads 업로드 시작 ({len(imgs)}장)...")
    print(f"  [ThreadsPublisher] 이미지 URL 베이스: {url_base}")

    caption = _build_caption(hook, hashtags)

    # 1. 각 이미지 캐러셀 아이템 컨테이너 생성
    container_ids: list[str] = []
    for i, img_path in enumerate(imgs, 1):
        img_url = f"{url_base}/{img_path.name}"
        print(f"  [ThreadsPublisher] 컨테이너 생성 ({i}/{len(imgs)}): {img_path.name}")
        cid = _create_carousel_item(img_url)
        print(f"    → container_id: {cid}")
        _wait_for_ready(cid)
        container_ids.append(cid)

    # 2. 캐러셀 컨테이너 생성
    print("  [ThreadsPublisher] 캐러셀 컨테이너 생성 중...")
    carousel_id = _create_carousel_container(container_ids, caption)
    print(f"    → carousel_id: {carousel_id}")
    _wait_for_ready(carousel_id)

    # 3. 게시
    print("  [ThreadsPublisher] 게시 중...")
    post_id = _publish_container(carousel_id)
    print(f"  [ThreadsPublisher] 업로드 완료! post_id: {post_id}")
    print(f"  [ThreadsPublisher] 확인: https://www.threads.net/t/{post_id}")

    return post_id


def _publish_single(
    img_path: Path,
    hook: str,
    hashtags: list[str],
    base_url: str = "",
) -> str:
    """단일 이미지를 Threads 단독 게시물로 업로드."""
    url_base = (base_url or _IMAGE_BASE_URL or "").rstrip("/")
    if not url_base:
        print("  [ThreadsPublisher] 공개 URL 없음 → ngrok 자동 터널 시작...")
        url_base = _get_public_url(img_path.parent).rstrip("/")

    img_url = f"{url_base}/{img_path.name}"
    caption = _build_caption(hook, hashtags)

    # 단일 이미지 컨테이너
    resp = httpx.post(
        f"{THREADS_API_BASE}/{THREADS_USER_ID}/threads",
        params={
            "media_type":   "IMAGE",
            "image_url":    img_url,
            "text":         caption,
            "access_token": THREADS_ACCESS_TOKEN,
        },
        timeout=30,
    )
    data = resp.json()
    if "id" not in data:
        raise RuntimeError(f"Threads 단일 이미지 컨테이너 생성 실패: {data}")

    container_id = data["id"]
    _wait_for_ready(container_id)

    post_id = _publish_container(container_id)
    print(f"  [ThreadsPublisher] 업로드 완료! post_id: {post_id}")
    print(f"  [ThreadsPublisher] 확인: https://www.threads.net/t/{post_id}")
    return post_id
