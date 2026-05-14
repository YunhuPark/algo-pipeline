"""
Phase 4 (선택): Instagram Graph API Publisher
생성된 카드뉴스 이미지를 인스타그램 캐러셀 게시물로 자동 업로드.

── 사전 요구사항 ─────────────────────────────────────────────────────
1. Meta Developer 앱 생성
   https://developers.facebook.com → '앱 만들기' → '비즈니스' 유형

2. Instagram 비즈니스/크리에이터 계정 연결
   → 개인 인스타 계정을 프로페셔널(비즈니스/크리에이터) 전환 후
   → Facebook 페이지와 연결

3. 필요한 권한 (Permissions):
   instagram_basic, instagram_content_publish, pages_read_engagement

4. 장기 액세스 토큰 발급 (60일 유효, 자동 갱신 코드 포함)
   https://developers.facebook.com/tools/explorer

5. Instagram 비즈니스 계정 ID 확인
   GET https://graph.facebook.com/me/accounts → 연결된 페이지 ID 확인
   GET https://graph.facebook.com/{page_id}?fields=instagram_business_account

6. .env에 아래 3개 항목 추가:
   IG_ACCESS_TOKEN=EAAxxxxxxxx...
   IG_USER_ID=17841xxxxxxxxx
   IG_IMAGE_BASE_URL=https://your-server.com/images/   ← 이미지 공개 URL (아래 참고)

── 이미지 공개 URL 옵션 ─────────────────────────────────────────────
Instagram Graph API는 로컬 파일 직접 업로드 불가 — 공개 URL이 필요.
옵션 A: ngrok (로컬 개발용)    → ngrok http 8080  → 임시 공개 URL 생성
옵션 B: Cloudflare Tunnel      → 무료 영구 터널
옵션 C: AWS S3 / GCS           → 생성 이미지를 버킷에 업로드 후 URL 사용 (권장)
옵션 D: 내장 HTTP 서버 (이 코드에 포함) → 로컬 서버 자동 시작 + ngrok 연동
─────────────────────────────────────────────────────────────────────
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import quote

import httpx
import requests

from src.config import OUTPUT_DIR


def _upload_to_catbox(image_path: Path) -> str:
    """이미지를 catbox.moe에 업로드하고 공개 HTTPS URL 반환."""
    with open(image_path, "rb") as f:
        r = requests.post(
            "https://catbox.moe/user/api.php",
            data={"reqtype": "fileupload"},
            files={"fileToUpload": (image_path.name, f, "image/png")},
            timeout=60,
        )
    url = r.text.strip()
    if not url.startswith("https://"):
        raise RuntimeError(f"catbox 업로드 실패: {url}")
    return url

# ── 환경 변수 ─────────────────────────────────────────────
IG_ACCESS_TOKEN  = os.getenv("IG_ACCESS_TOKEN", "")
IG_USER_ID       = os.getenv("IG_USER_ID", "")
IG_IMAGE_BASE_URL = os.getenv("IG_IMAGE_BASE_URL", "")  # 공개 이미지 URL 베이스

GRAPH_BASE = "https://graph.instagram.com/v21.0"


# ── 로컬 이미지 서버 + ngrok 자동 터널 ───────────────────

class _QuietHTTPHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_): pass  # 로그 숨김

    def send_response(self, code, message=None):
        super().send_response(code, message)

    def end_headers(self):
        self.send_header("Content-Type", "image/png")
        super().end_headers()


def _start_local_server(directory: Path, port: int = 8765) -> str:
    """지정 디렉터리를 로컬 HTTP 서버로 노출."""
    # UTF-8 환경에서 한글 경로 처리
    dir_str = str(directory.resolve())
    os.chdir(dir_str)
    handler = _QuietHTTPHandler
    handler.extensions_map = {'.png': 'image/png', '.mp4': 'video/mp4', '': 'application/octet-stream'}
    server = HTTPServer(("", port), handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    print(f"  [Publisher] 로컬 서버 시작: http://localhost:{port}")
    return f"http://localhost:{port}"


def _ensure_pyngrok() -> None:
    """pyngrok이 없으면 자동 설치."""
    try:
        import pyngrok  # noqa: F401
    except ImportError:
        print("  [Publisher] pyngrok 설치 중...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyngrok", "-q"])
        print("  [Publisher] pyngrok 설치 완료")


def _start_ngrok_tunnel(port: int = 8765) -> str:
    """
    ngrok HTTP 터널을 열어 공개 URL 반환.
    pyngrok 자동 설치 포함.
    """
    _ensure_pyngrok()
    from pyngrok import ngrok, conf  # noqa: PLC0415

    # 이미 열린 터널 재활용
    existing = ngrok.get_tunnels()
    for tunnel in existing:
        if str(port) in tunnel.config.get("addr", ""):
            print(f"  [Publisher] 기존 ngrok 터널 재활용: {tunnel.public_url}")
            return tunnel.public_url.replace("http://", "https://")

    tunnel = ngrok.connect(port, "http")
    public_url = tunnel.public_url.replace("http://", "https://")
    print(f"  [Publisher] ngrok 터널 시작: {public_url}")
    return public_url


def _get_public_url(image_dir: Path, port: int = 8765) -> str:
    """
    로컬 서버 시작 + ngrok 터널 → 공개 URL 반환.
    image_dir 자체를 서빙해 파일명만 URL에 붙이면 됨.
    """
    _start_local_server(image_dir, port)
    public_base = _start_ngrok_tunnel(port)
    return public_base


# ── Instagram Graph API ───────────────────────────────────

def _create_media_container(media_url: str, is_carousel_item: bool = True, is_video: bool = False) -> str:
    """단일 이미지/동영상 미디어 컨테이너 생성 → container_id 반환"""
    params = {
        "is_carousel_item": "true" if is_carousel_item else "false",
        "access_token":     IG_ACCESS_TOKEN,
    }
    if is_video:
        params["media_type"] = "VIDEO"
        params["video_url"] = media_url
    else:
        params["image_url"] = media_url

    resp = httpx.post(
        f"{GRAPH_BASE}/{IG_USER_ID}/media",
        params=params,
        timeout=30,
    )
    data = resp.json()
    if "id" not in data:
        raise RuntimeError(f"미디어 컨테이너 생성 실패: {data}")
    return data["id"]


def _create_carousel_container(
    container_ids: list[str],
    caption: str,
) -> str:
    """캐러셀 컨테이너 생성 → carousel_container_id 반환"""
    resp = httpx.post(
        f"{GRAPH_BASE}/{IG_USER_ID}/media",
        params={
            "media_type":   "CAROUSEL",
            "children":     ",".join(container_ids),
            "caption":      caption,
            "access_token": IG_ACCESS_TOKEN,
        },
        timeout=30,
    )
    data = resp.json()
    if "id" not in data:
        raise RuntimeError(f"캐러셀 컨테이너 생성 실패: {data}")
    return data["id"]


def _wait_for_ready(container_id: str, max_wait: int = 60) -> None:
    """컨테이너가 FINISHED 상태가 될 때까지 대기"""
    for _ in range(max_wait // 3):
        time.sleep(3)
        resp = httpx.get(
            f"{GRAPH_BASE}/{container_id}",
            params={"fields": "status_code", "access_token": IG_ACCESS_TOKEN},
            timeout=10,
        )
        status = resp.json().get("status_code", "")
        if status == "FINISHED":
            return
        if status == "ERROR":
            raise RuntimeError(f"미디어 처리 실패 (container: {container_id})")
    raise TimeoutError(f"미디어 처리 타임아웃 (container: {container_id})")


def _publish_carousel(carousel_container_id: str) -> str:
    """캐러셀 게시 → post_id 반환"""
    resp = httpx.post(
        f"{GRAPH_BASE}/{IG_USER_ID}/media_publish",
        params={
            "creation_id":  carousel_container_id,
            "access_token": IG_ACCESS_TOKEN,
        },
        timeout=30,
    )
    data = resp.json()
    if "id" not in data:
        raise RuntimeError(f"게시 실패: {data}")
    return data["id"]


def get_post_permalink(post_id: str) -> str:
    """게시된 포스트의 실제 Instagram URL 조회 (shortcode 기반)"""
    resp = httpx.get(
        f"{GRAPH_BASE}/{post_id}",
        params={"fields": "permalink", "access_token": IG_ACCESS_TOKEN},
        timeout=10,
    )
    return resp.json().get("permalink", "")


def _build_caption(hook: str, hashtags: list[str]) -> str:
    """인스타그램 캡션 조합 (후킹 문구 + 줄바꿈 + 해시태그)"""
    tag_str = " ".join(hashtags)
    return f"{hook}\n\n.\n.\n.\n{tag_str}"


# ── 공개 인터페이스 ───────────────────────────────────────

def publish(
    image_paths: list[Path],
    hook: str,
    hashtags: list[str],
    base_url: str = "",
) -> str:
    """
    카드뉴스 이미지들을 Instagram 캐러셀로 게시.

    Args:
        image_paths: PNG 파일 경로 목록 (card_01_cover.png ~ card_N_cta.png)
        hook:        캡션 첫 줄 후킹 문구
        hashtags:    해시태그 목록
        base_url:    이미지 공개 URL 베이스 (비어있으면 .env IG_IMAGE_BASE_URL 사용)
    Returns:
        업로드된 게시물 ID
    """
    if not IG_ACCESS_TOKEN or not IG_USER_ID:
        raise EnvironmentError(
            ".env에 IG_ACCESS_TOKEN과 IG_USER_ID가 없습니다.\n"
            "설정 가이드: src/agents/publisher.py 상단 주석 참고"
        )

    # PNG 및 MP4 필터 (hashtags.txt 등 제외)
    imgs = [p for p in image_paths if p.suffix.lower() in [".png", ".mp4"]]
    if not imgs:
        raise ValueError("업로드할 미디어(PNG/MP4) 파일이 없습니다.")

    # 공개 URL 결정: catbox → 명시 URL → .env → ngrok 자동 시작
    url_base = (base_url or IG_IMAGE_BASE_URL or "").rstrip("/")
    use_catbox = url_base.lower() in ("catbox", "catbox://", "")

    print(f"\n  [Publisher] Instagram 업로드 시작 ({len(imgs)}장)...")

    # 1. 각 이미지/비디오 미디어 컨테이너 생성
    container_ids: list[str] = []
    for img_path in imgs:
        is_video = img_path.suffix.lower() == ".mp4"
        if use_catbox:
            print(f"  [Publisher] catbox 업로드: {img_path.name}...", end=" ", flush=True)
            img_url = _upload_to_catbox(img_path)
            print(img_url)
        else:
            img_url = f"{url_base}/output_img/{quote(img_path.parent.name)}/{quote(img_path.name)}"
            print(f"  [Publisher] 컨테이너 생성 (video={is_video}): {img_path.name}")
        cid = _create_media_container(img_url, is_carousel_item=True, is_video=is_video)
        print(f"    → container_id: {cid}")
        _wait_for_ready(cid)
        container_ids.append(cid)

    # 2. 캐러셀 컨테이너 생성
    caption = _build_caption(hook, hashtags)
    print(f"  [Publisher] 캐러셀 컨테이너 생성...")
    carousel_id = _create_carousel_container(container_ids, caption)
    print(f"    → carousel_id: {carousel_id}")
    _wait_for_ready(carousel_id)

    # 3. 게시
    print(f"  [Publisher] 게시 중...")
    post_id = _publish_carousel(carousel_id)
    print(f"  [Publisher] 업로드 완료! post_id: {post_id}")

    permalink = get_post_permalink(post_id)
    if permalink:
        print(f"  [Publisher] 확인: {permalink}")
    else:
        print(f"  [Publisher] 확인: https://www.instagram.com/ (post_id: {post_id})")

    return post_id


def check_token_status() -> dict:
    """액세스 토큰 상태 및 만료일 확인"""
    resp = httpx.get(
        f"{GRAPH_BASE}/debug_token",
        params={
            "input_token":  IG_ACCESS_TOKEN,
            "access_token": IG_ACCESS_TOKEN,
        },
        timeout=10,
    )
    return resp.json().get("data", {})


def refresh_long_lived_token(short_lived_token: str, app_id: str, app_secret: str) -> str:
    """
    단기 토큰 → 장기 액세스 토큰 (60일) 변환
    """
    resp = httpx.get(
        f"{GRAPH_BASE}/oauth/access_token",
        params={
            "grant_type":        "fb_exchange_token",
            "client_id":         app_id,
            "client_secret":     app_secret,
            "fb_exchange_token": short_lived_token,
        },
        timeout=10,
    )
    data = resp.json()
    if "access_token" not in data:
        raise RuntimeError(f"토큰 갱신 실패: {data}")
    return data["access_token"]
