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
                    f"Rules: dark/moody/cinematic style preferred, no people if possible, "
                    f"abstract or technology backgrounds work best for card news overlays."
                )
            }]
        )
        query = resp.choices[0].message.content.strip().strip('"')
        return query
    except Exception:
        # fallback: 간단한 영문 변환
        fallback_map = {
            "AI": "artificial intelligence technology futuristic dark",
            "인공지능": "artificial intelligence neural network abstract",
            "ChatGPT": "chatbot AI robot technology",
            "앱": "mobile app smartphone technology",
            "개발": "software development code dark screen",
            "스타트업": "startup technology modern office",
            "테크": "technology digital future abstract",
            "경제": "economy finance business data",
            "환경": "nature environment sustainability green",
            "건강": "health wellness lifestyle minimal",
            "보안": "cybersecurity dark digital protection",
            "메타버스": "metaverse virtual reality digital world",
            "블록체인": "blockchain cryptocurrency digital abstract",
        }
        for k, v in fallback_map.items():
            if k in topic:
                return v
        return f"{topic} technology abstract dark background"


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


def _pick_darkest(photos: list, n: int = 10) -> dict | None:
    """
    Pexels 사진 목록에서 가장 어두운 사진 선택 (텍스트 가독성 최적화).
    각 사진을 다운로드해 밝기 비교 후 최적 선택.
    """
    candidates = []
    for photo in photos[:n]:
        url = photo["src"]["medium"]  # 빠른 밝기 체크용 저해상도
        try:
            r = httpx.get(url, timeout=10, follow_redirects=True)
            if r.status_code == 200:
                img = Image.open(io.BytesIO(r.content)).convert("RGB")
                brightness = _average_brightness(img)
                candidates.append((brightness, photo))
        except Exception:
            continue

    if not candidates:
        return photos[0] if photos else None

    # 밝기 오름차순 정렬 → 가장 어두운 것 선택
    candidates.sort(key=lambda x: x[0])
    chosen_brightness, chosen = candidates[0]
    print(f"  [Pexels] 밝기 분석: {len(candidates)}장 중 평균밝기 {chosen_brightness:.0f}/255인 사진 선택")
    return chosen


# ── Pexels ────────────────────────────────────────────────

def search_pexels(topic: str, randomize: bool = False, page: int = 1) -> Image.Image | None:
    """
    Pexels API로 주제 관련 고화질 사진 검색.
    상위 10장 중 가장 어두운 사진을 선택해 텍스트 가독성 최적화.

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
                "per_page": 10,      # 10장 수집 후 가장 어두운 것 선택
                "page": page,
                "color": "black",   # 어두운 톤 우선
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
            # 기본 모드: 밝기 분석 후 가장 어두운 사진 선택
            photo = _pick_darkest(photos, n=10) or photos[0]

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
                    f"Write a DALL-E 3 image generation prompt for a dark, cinematic background "
                    f"image suitable for a Korean tech card news about '{topic}'. "
                    f"Style: dark navy/purple tones, cinematic lighting, abstract futuristic, "
                    f"NO text, NO UI, full-bleed background. Max 80 words."
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
