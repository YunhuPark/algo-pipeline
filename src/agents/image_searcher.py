"""
Image Searcher — 주제에 맞는 배경 이미지 자동 소싱

우선순위:
  1. Pexels API  — 실제 고화질 사진 (PEXELS_API_KEY 설정 시 자동 사용)
  2. DALL-E 3    — AI 생성 배경 (OPENAI_API_KEY 이미 있음, Pexels 실패 시 fallback)

GPT-4o로 한국어 주제 → 최적 영문 검색어 자동 변환.
결과는 data/bg_cache/ 에 캐시하여 중복 API 호출 방지.
"""
from __future__ import annotations

import io
import random
from pathlib import Path

import httpx
from PIL import Image, ImageFilter

from src.config import OPENAI_API_KEY, PEXELS_API_KEY, DATA_DIR

TARGET_W, TARGET_H = 1080, 1350  # Instagram 4:5


# ── 배경 스타일 ───────────────────────────────────────────
# 밝은 배경을 시험해 봤지만 주제와 무관한 화려한 추상 사진이 뽑혀서, 어두운 기술
# 사진이 더 무난하다는 판단으로 기본을 dark로 되돌렸다. 밝게 쓰려면 BACKGROUND_STYLE=bright.

def _bright_style() -> bool:
    import os

    return os.getenv("BACKGROUND_STYLE", "dark").strip().lower() == "bright"


# ── 쿼리 생성 ─────────────────────────────────────────────

def _generate_pexels_query(topic: str) -> str:
    """GPT-4o를 사용해 한국어 주제에 최적화된 Pexels 영문 검색어 생성"""
    try:
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)
        resp = client.chat.completions.create(
            model="gpt-4o-mini",  # 빠르고 저렴한 모델 사용
            max_tokens=60,
            messages=[{
                "role": "user",
                "content": (
                    f"Convert this Korean topic to an optimal English Pexels photo search query. "
                    f"Return ONLY the search query (5-8 words), no explanation.\n"
                    f"Topic: '{topic}'\n"
                    + (
                        "Rules: bright, vivid, colorful and clean look (NOT dark or moody), "
                        if _bright_style()
                        else "Rules: dark/moody/cinematic style, "
                    )
                    + "focused on objects, devices or abstract technology, no people or faces, "
                    "works as a card news background under bold text."
                )
            }]
        )
        query = resp.choices[0].message.content.strip().strip('"')
        return query
    except Exception:
        # fallback: 간단한 영문 변환
        fallback_map = {
            "AI": "artificial intelligence technology futuristic colorful",
            "인공지능": "artificial intelligence neural network abstract",
            "ChatGPT": "chatbot AI robot technology",
            "앱": "mobile app smartphone technology",
            "개발": "software development code colorful screen",
            "스타트업": "startup technology modern office",
            "테크": "technology digital future abstract",
            "경제": "economy finance business data",
            "환경": "nature environment sustainability green",
            "건강": "health wellness lifestyle minimal",
            "보안": "cybersecurity digital protection blue lock",
            "메타버스": "metaverse virtual reality digital world",
            "블록체인": "blockchain cryptocurrency digital abstract",
        }
        for k, v in fallback_map.items():
            if k in topic:
                return v
        return f"{topic} technology abstract bright colorful background"


# ── 이미지 처리 ───────────────────────────────────────────

def _resize_and_crop(img: Image.Image, w: int = TARGET_W, h: int = TARGET_H) -> Image.Image:
    """비율 유지 크롭 → 정확한 사이즈"""
    ratio = max(w / img.width, h / img.height)
    new_w, new_h = int(img.width * ratio), int(img.height * ratio)
    img = img.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - w) // 2
    top  = (new_h - h) // 2
    return img.crop((left, top, left + w, top + h))


def _average_brightness(img: Image.Image) -> float:
    """이미지 평균 밝기 반환 (0=완전 검정, 255=완전 흰색). 빠른 계산을 위해 썸네일 사용."""
    thumb = img.copy().convert("L").resize((64, 64), Image.BILINEAR)
    pixels = list(thumb.getdata())
    return sum(pixels) / len(pixels)


def _photo_score(img: Image.Image) -> tuple[float, float, float]:
    """(점수, 평균밝기, 평균채도). 중간 밝기이면서 색감이 있는 사진일수록 높다.

    예전에는 "가장 어두운 사진"을 골랐다. 글자가 얇고 뒤에 받침이 없던 시절의
    가독성 대책이었는데, 지금은 굵은 글자·윤곽선·반투명 상자가 가독성을 맡으므로
    배경이 어두울 이유가 없고 오히려 카드가 칠흑처럼 보이는 원인이었다. 너무
    어두운 사진과 하얗게 날아간 사진은 모두 감점한다.
    """
    import colorsys

    thumb = img.copy().convert("RGB").resize((48, 48), Image.BILINEAR)
    pixels = list(thumb.getdata())
    brightness = sum(0.299 * r + 0.587 * g + 0.114 * b for r, g, b in pixels) / len(pixels)
    saturation = sum(colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)[1] for r, g, b in pixels) / len(pixels)
    closeness = 1.0 - min(1.0, abs(brightness - 115) / 115)   # 115 근처가 최고점(흰 글자가 묻히지 않는 밝기)
    return closeness + 0.9 * min(saturation, 0.6), brightness, saturation


def _pick_best(photos: list, n: int = 30, bright: bool = False) -> dict | None:
    """Pexels 후보를 병렬로 내려받아 고른다.

    bright=False(기본)면 평균 밝기가 가장 낮은 사진, True면 _photo_score가 가장 높은 사진.
    """
    from concurrent.futures import ThreadPoolExecutor

    def _score(photo):
        try:
            r = httpx.get(photo["src"]["medium"], timeout=10, follow_redirects=True)
            if r.status_code != 200:
                return None
            img = Image.open(io.BytesIO(r.content)).convert("RGB")
            score, brightness, saturation = _photo_score(img)
            return (score if bright else -brightness, brightness, saturation, photo)
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=6) as pool:
        scored = [x for x in pool.map(_score, photos[:n]) if x]
    if not scored:
        return photos[0] if photos else None
    score, brightness, saturation, chosen = max(scored, key=lambda x: x[0])
    print(
        f"  [Pexels] 밝기 분석({'bright' if bright else 'dark'}): {len(scored)}장 중 "
        f"밝기 {brightness:.0f}/255, 채도 {saturation:.2f}인 사진 선택"
    )
    return chosen


# ── Pexels ────────────────────────────────────────────────

def search_pexels(topic: str, randomize: bool = False, page: int = 1) -> Image.Image | None:
    """
    Pexels API로 주제 관련 고화질 사진 검색.
    상위 30장 중 중간 밝기에 색감이 있는 사진을 선택 (밝고 선명한 카드 배경).

    Args:
        topic:      한국어 주제
        randomize:  True면 상위 5장 중 무작위 선택 (매 실행마다 다른 사진)
        page:       결과 페이지 번호 (force_refresh 재시도 시 순환)
    """
    if not PEXELS_API_KEY:
        return None

    query = _generate_pexels_query(topic)
    print(f"  [Pexels] 검색어: '{query}' (page={page})")

    try:
        resp = httpx.get(
            "https://api.pexels.com/v1/search",
            headers={"Authorization": PEXELS_API_KEY},
            params={
                "query": query,
                "orientation": "portrait",
                "size": "large",
                "per_page": 30,      # 후보를 넉넉히 받아 스타일에 맞는 것 선택
                "page": page,
                **({} if _bright_style() else {"color": "black"}),   # dark: 어두운 톤 우선
            },
            timeout=15,
        )
        resp.raise_for_status()
        photos = resp.json().get("photos", [])
        if not photos:
            print(f"  [Pexels] 검색 결과 없음, DALL-E로 전환")
            return None

        if randomize:
            # randomize 모드: 상위 5장 중 무작위
            photo = random.choice(photos[:5])
        else:
            # dark(기본): 가장 어두운 사진 / bright: 중간 밝기이면서 색감 있는 사진
            photo = _pick_best(photos, n=30, bright=_bright_style()) or photos[0]

        photo_url = photo["src"]["large2x"]
        alt = photo.get("alt", "no description")[:60]
        photographer = photo.get("photographer", "unknown")
        print(f"  [Pexels] 최종 선택: '{alt}' by {photographer}")

        img_resp = httpx.get(photo_url, timeout=30, follow_redirects=True)
        img_resp.raise_for_status()
        img = Image.open(io.BytesIO(img_resp.content)).convert("RGB")
        return _resize_and_crop(img)

    except Exception as e:
        print(f"  [Pexels] 실패 ({e}), DALL-E로 전환")
        return None


_PEXELS_VIDEO_CACHE_DIR = DATA_DIR / "pexels_video_cache"


def search_pexels_video(
    topic: str, page: int = 1, min_duration: int = 4, max_duration: int = 20
) -> tuple[Image.Image, Path] | None:
    """Pexels 영상 검색 — 유튜브에 실제로 맞는 영상이 없는 슬라이드용 대체 클립.

    엉뚱한 유튜브 영상을 억지로 붙이는 대신, 주제와 맞는 짧은(4~20초) 세로
    스톡 영상을 다운받아 돌려준다. 반환값은 (카드 렌더링용 미리보기 이미지,
    로컬에 저장된 mp4 경로) — 실패하면 None (호출부는 정적 사진으로 대체).
    """
    if not PEXELS_API_KEY:
        return None

    query = _generate_pexels_query(topic)
    print(f"  [Pexels] 영상 검색어: '{query}' (page={page})")

    try:
        resp = httpx.get(
            "https://api.pexels.com/videos/search",
            headers={"Authorization": PEXELS_API_KEY},
            params={
                "query": query,
                "orientation": "portrait",
                "size": "medium",
                "per_page": 10,
                "page": page,
            },
            timeout=15,
        )
        resp.raise_for_status()
        videos = resp.json().get("videos", [])
        if not videos:
            print("  [Pexels] 영상 검색 결과 없음")
            return None

        # 카드 한 장에 넣을 용도라 너무 길거나 짧은 클립은 제외한다.
        # 조건을 만족하는 게 없으면 그냥 상위 결과를 그대로 쓴다.
        candidates = [v for v in videos if min_duration <= v.get("duration", 0) <= max_duration]
        video = (candidates or videos)[0]

        # 세로(9:16에 가까운) 파일 중 가장 작은 해상도 — 카드 폭(1080px)이면
        # 충분하고, 큰 파일을 받으면 다운로드·인코딩 시간만 늘어난다.
        files = sorted(
            (
                f for f in video.get("video_files", [])
                if f.get("width") and f.get("height") and f["height"] >= f["width"]
            ),
            key=lambda f: f.get("width", 9999),
        )
        if not files:
            files = sorted(video.get("video_files", []), key=lambda f: f.get("width", 9999))
        if not files:
            return None

        cache_dir = _PEXELS_VIDEO_CACHE_DIR
        cache_dir.mkdir(parents=True, exist_ok=True)
        out_path = cache_dir / f"pexels_{video['id']}.mp4"
        if not out_path.exists():
            vid_resp = httpx.get(files[0]["link"], timeout=60, follow_redirects=True)
            vid_resp.raise_for_status()
            out_path.write_bytes(vid_resp.content)

        preview_img = None
        preview_url = video.get("image", "")
        if preview_url:
            pr = httpx.get(preview_url, timeout=20, follow_redirects=True)
            if pr.status_code == 200:
                preview_img = Image.open(io.BytesIO(pr.content)).convert("RGB")
        if preview_img is None:
            return None

        photographer = video.get("user", {}).get("name", "unknown")
        print(
            f"  [Pexels] 영상 선택: id={video['id']} by {photographer} "
            f"({video.get('duration', '?')}초)"
        )
        return preview_img, out_path

    except Exception as e:
        print(f"  [Pexels] 영상 검색 실패 ({e})")
        return None


# ── DALL-E 3 ─────────────────────────────────────────────

def generate_dalle_background(topic: str) -> Image.Image:
    """DALL-E 3으로 주제 맞춤 AI 배경 생성"""
    from openai import OpenAI

    client = OpenAI(api_key=OPENAI_API_KEY)

    # GPT-4o로 DALL-E 프롬프트 최적화
    try:
        opt = client.chat.completions.create(
            model="gpt-4o-mini",
            max_tokens=120,
            messages=[{
                "role": "user",
                "content": (
                    + (
                        f"Write a DALL-E 3 image generation prompt for a bright, vivid, colorful "
                        f"background image suitable for a Korean tech card news about '{topic}'. "
                        f"Style: clean modern look, saturated blue/teal/magenta accents, soft studio "
                        f"lighting, abstract futuristic, NO text, NO UI, NO people, full-bleed "
                        f"background. Max 80 words."
                        if _bright_style()
                        else f"Write a DALL-E 3 image generation prompt for a dark, cinematic background "
                        f"image suitable for a Korean tech card news about '{topic}'. "
                        f"Style: dark navy/purple tones, cinematic lighting, abstract futuristic, "
                        f"NO text, NO UI, NO people, full-bleed background. Max 80 words."
                    )
                )
            }]
        )
        prompt = opt.choices[0].message.content.strip()
    except Exception:
        prompt = (
            f"Dark cinematic abstract background for '{topic}' card news. "
            "Deep navy blue and purple tones, cinematic dramatic lighting, "
            "futuristic technology aesthetic, glowing particles, no text, no UI elements."
        )

    print(f"  [DALL-E] 프롬프트: {prompt[:80]}...")
    import base64 as _b64
    response = client.images.generate(
        model="gpt-image-1",
        prompt=prompt,
        size="1024x1536",
        quality="medium",
        n=1,
    )

    img_data = response.data[0]
    if getattr(img_data, "b64_json", None):
        img = Image.open(io.BytesIO(_b64.b64decode(img_data.b64_json))).convert("RGB")
    else:
        img_url = img_data.url
        img_resp = httpx.get(img_url, timeout=60, follow_redirects=True)
        img = Image.open(io.BytesIO(img_resp.content)).convert("RGB")
    return _resize_and_crop(img)


# ── 공개 인터페이스 ───────────────────────────────────────

def get_background_image(
    topic: str,
    force_dalle: bool = False,
    force_refresh: bool = False,
    randomize: bool = False,
    retry_num: int = 0,
) -> Image.Image:
    """
    배경 이미지를 가져온다. 캐시 우선, 없으면 Pexels → DALL-E 순.
    재시도 시 Pexels 페이지를 순환해 다른 이미지를 선택.

    Args:
        topic:         한국어 주제
        force_dalle:   True → Pexels 건너뛰고 DALL-E 직접 사용
        force_refresh: True → 캐시 무시하고 새로 검색/생성
        randomize:     True → Pexels 상위 5장 중 무작위 선택
        retry_num:     재시도 횟수 (Pexels 페이지 순환용)
    """
    cache_dir = DATA_DIR / "bg_cache"
    cache_dir.mkdir(exist_ok=True)
    safe = "".join(c if c.isalnum() else "_" for c in topic)[:30]
    source = "dalle" if force_dalle or not PEXELS_API_KEY else "pexels"
    # 재시도 시 캐시 키에 페이지 번호 포함 → 다른 이미지 사용
    cache_suffix = f"_p{retry_num}" if retry_num > 0 else ""
    if _bright_style():
        cache_suffix = f"_bright{cache_suffix}"
    cache_path = cache_dir / f"{safe}_{source}{cache_suffix}.jpg"

    if cache_path.exists() and not force_refresh:
        print(f"  [ImageSearcher] 캐시 사용: {cache_path.name}")
        return Image.open(cache_path).convert("RGB")

    img: Image.Image | None = None
    if not force_dalle:
        # 재시도 횟수에 따라 Pexels 페이지 순환 (1→2→3...)
        pexels_page = retry_num + 1
        img = search_pexels(topic, randomize=randomize, page=pexels_page)

    if img is None:
        print(f"  [ImageSearcher] DALL-E 3 배경 생성 중...")
        img = generate_dalle_background(topic)

    img.save(str(cache_path), "JPEG", quality=95)
    print(f"  [ImageSearcher] 캐시 저장: {cache_path.name}")
    return img
