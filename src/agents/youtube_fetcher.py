"""
YouTube 썸네일 + 영상 메타데이터 자동 수집
기사 키워드로 유튜브 영상을 검색해 썸네일 이미지 + 제목/채널/설명을 반환한다.
별도 API 키 없이 Tavily 검색 + oembed + 공개 썸네일 URL 방식 사용.
"""
from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import httpx
from PIL import Image

from src.config import TAVILY_API_KEY, DATA_DIR

_CACHE_DIR = DATA_DIR / "yt_cache"
_CACHE_DIR.mkdir(parents=True, exist_ok=True)
_TRANSCRIPT_CACHE_DIR = _CACHE_DIR / "transcripts"
_TRANSCRIPT_CACHE_DIR.mkdir(parents=True, exist_ok=True)

# 영상 메타데이터 캐시 (video_id → dict) — yt-dlp 중복 호출 방지
_video_meta_cache: dict[str, dict] = {}
# 자막 인메모리 캐시 (video_id → transcript str) — 프로세스 내 중복 다운로드 방지
_transcript_cache: dict[str, str] = {}
# 마지막 자막 다운로드 시각 — 요청 간격 조절용
_last_transcript_fetch: float = 0.0
_TRANSCRIPT_INTERVAL = 4.0  # 자막 요청 최소 간격(초) — 429 rate limit 방지


@dataclass
class VideoInfo:
    """유튜브 영상 1건의 메타데이터 + 썸네일 + 실제 영상 경로"""
    video_id: str
    url: str
    title: str
    creator: str = ""      # 채널명 (oembed)
    snippet: str = ""      # Tavily 검색 스니펫 (영상 설명 요약)
    thumbnail: Image.Image | None = field(default=None, repr=False)
    video_path: Path | None = field(default=None, repr=False)
    view_count: int = 0    # 조회수 (검증 후 채워짐)
    duration: int = 0      # 영상 길이 (초)
    start_seconds: int = 0 # 슬라이드 내용 관련 구간 시작 시간(초)


def _extract_video_id(url: str) -> str | None:
    """유튜브 URL에서 video ID 추출"""
    patterns = [
        r"youtube\.com/watch\?v=([A-Za-z0-9_-]{11})",
        r"youtu\.be/([A-Za-z0-9_-]{11})",
        r"youtube\.com/embed/([A-Za-z0-9_-]{11})",
    ]
    for p in patterns:
        m = re.search(p, url)
        if m:
            return m.group(1)
    return None


def _download_thumbnail(video_id: str) -> Image.Image | None:
    """유튜브 video ID로 최고화질 썸네일 다운로드"""
    cache_path = _CACHE_DIR / f"{video_id}.jpg"
    if cache_path.exists():
        return Image.open(cache_path).convert("RGB")

    # 화질 순서대로 시도
    qualities = ["maxresdefault", "sddefault", "hqdefault", "mqdefault"]
    for q in qualities:
        url = f"https://img.youtube.com/vi/{video_id}/{q}.jpg"
        try:
            r = httpx.get(url, timeout=10, follow_redirects=True)
            if r.status_code == 200 and len(r.content) > 5000:
                cache_path.write_bytes(r.content)
                img = Image.open(cache_path).convert("RGB")
                # 120x90 기본 이미지(검은 썸네일) 제외
                if img.size[0] > 200:
                    return img
        except Exception:
            continue
    return None


def _get_video_metadata(video_id: str) -> dict:
    """
    yt-dlp로 영상 메타데이터 조회 (가용성·조회수·챕터·설명 등).
    결과를 모듈 캐시에 저장해 중복 호출 방지.
    """
    if video_id in _video_meta_cache:
        return _video_meta_cache[video_id]

    result: dict = {
        'available': False, 'view_count': 0, 'duration': 0,
        'channel': '', 'chapters': [], 'description': '',
    }
    try:
        import yt_dlp
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'skip_download': True,
            'socket_timeout': 15,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(
                f'https://www.youtube.com/watch?v={video_id}',
                download=False,
            )
        result = {
            'available': True,
            'view_count': info.get('view_count') or 0,
            'duration': info.get('duration') or 0,
            'channel': info.get('channel') or info.get('uploader', ''),
            'channel_follower_count': info.get('channel_follower_count') or 0,
            'chapters': [
                {
                    'title': c.get('title', ''),
                    'start_time': int(c.get('start_time', 0)),
                    'end_time': int(c.get('end_time', 0)),
                }
                for c in (info.get('chapters') or [])
            ],
            'description': (info.get('description') or '')[:3000],
        }
    except Exception as e:
        result['error'] = str(e)

    _video_meta_cache[video_id] = result
    return result


def _parse_description_timestamps(description: str) -> list[dict]:
    """
    영상 설명에서 챕터 형식 타임스탬프 파싱.
    예: "0:30 Introduction", "1:23:45 - Demo", "2:15 | 핵심 기능"
    반환: [{'start_time': 초, 'title': 제목}, ...]
    """
    results = []
    ts_re = re.compile(
        r'^(?:(\d{1,2}):)?(\d{1,2}):(\d{2})\s*[-–—|·]?\s*(.{3,80})$'
    )
    for line in description.split('\n'):
        m = ts_re.match(line.strip())
        if m:
            h, mi, s, title = m.groups()
            total_s = int(h or 0) * 3600 + int(mi) * 60 + int(s)
            results.append({'start_time': total_s, 'title': title.strip()})
    return results


_SNIPPET_DEAD_SIGNALS = [
    "deleted", "removed", "unavailable", "private",
    "this video", "no longer available", "account terminated",
]
_SNIPPET_DURATION_RE = re.compile(
    r'\b(\d{1,2}):(\d{2}):(\d{2})\b|\b(\d{1,3})\s*(?:minutes?|mins?|시간|분)\b'
)


def _snippet_prefilter(candidates: list[VideoInfo]) -> list[VideoInfo]:
    """
    yt-dlp 없이 스니펫/제목 텍스트로 1차 필터링.
    - 삭제/비공개 시그널 제목 제거
    - 스니펫에서 영상 길이 파싱 가능하면 45분 초과 사전 제거
    - 중복 video_id 제거
    반환: 최대 8개 우선순위 후보
    """
    seen: set[str] = set()
    result: list[VideoInfo] = []
    for v in candidates:
        if v.video_id in seen:
            continue
        seen.add(v.video_id)

        title_lower = v.title.lower()
        snippet_lower = (v.snippet or "").lower()

        # 삭제/비공개 시그널 감지
        if any(sig in title_lower or sig in snippet_lower for sig in _SNIPPET_DEAD_SIGNALS):
            print(f"  [YouTubeFetcher] ✗ 스니펫 삭제 시그널: '{v.title[:35]}'")
            continue

        result.append(v)
        if len(result) >= 8:   # 검증 대상 최대 8개로 제한
            break

    return result


def _validate_candidates(
    candidates: list[VideoInfo],
    min_views: int = 10000,
    max_duration: int = 2700,   # 45분 이하
    max_workers: int = 4,
    limit: int = 8,
) -> list[VideoInfo]:
    """
    후보 영상 목록 검증:
    1차) 스니펫 텍스트 사전 필터 (yt-dlp 없이 빠르게)
    2차) yt-dlp 병렬 검증 (비공개/삭제·조회수·길이 필터)
    반환: 유효 영상 목록 (조회수 내림차순 정렬)
    """
    # 1차 스니펫 필터 — yt-dlp 호출 전 빠른 제거
    prefiltered = _snippet_prefilter(candidates)
    targets = prefiltered[:limit]
    print(f"  [YouTubeFetcher] 스니펫 1차 필터: {len(candidates)}개 → {len(targets)}개 → yt-dlp 검증")

    def _validate_one(v: VideoInfo) -> VideoInfo | None:
        meta = _get_video_metadata(v.video_id)
        if not meta.get('available', False):
            err = meta.get('error', 'unavailable')
            print(f"  [YouTubeFetcher] ✗ '{v.video_id}' 접근 불가 ({err[:40]})")
            return None
        vc = meta.get('view_count', 0)
        dur = meta.get('duration', 0)
        if vc < min_views:
            print(f"  [YouTubeFetcher] ✗ '{v.title[:30]}' 조회수 {vc:,} < {min_views:,}")
            return None
        if dur > 0 and (dur < 60 or dur > max_duration):
            print(f"  [YouTubeFetcher] ✗ '{v.title[:30]}' 길이 {dur}초 범위 초과")
            return None
        v.view_count = vc
        v.duration = dur
        if not v.creator:
            v.creator = meta.get('channel', '')
        print(f"  [YouTubeFetcher] ✓ '{v.title[:40]}' 조회수 {vc:,} / {dur//60}분")
        return v

    valid: list[VideoInfo] = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_validate_one, v): v for v in targets}
        for future in as_completed(futures):
            res = future.result()
            if res is not None:
                valid.append(res)

    valid.sort(key=lambda v: v.view_count, reverse=True)
    return valid


def get_video_chapters(video_id: str) -> list[dict]:
    """yt-dlp로 영상 챕터 정보 가져오기 (캐시 활용, 다운로드 없음)"""
    meta = _get_video_metadata(video_id)
    if not meta.get('available', False):
        print(f"  [YouTubeFetcher] '{video_id}' 영상 접근 불가: {meta.get('error', 'unavailable')}")
        return []
    return meta.get('chapters', [])


def _get_video_transcript(video_id: str, max_chars: int = 4000) -> str:
    """
    yt-dlp로 자동 생성 자막(auto-subtitle) 가져오기.
    영어 자막 우선, 없으면 한국어 시도.
    반환: "HH:MM:SS text\\n..." 형태의 문자열 (타임코드 포함)

    캐시 순서:
      1) 인메모리 캐시 (_transcript_cache) — 프로세스 내 즉시 반환
      2) 파일 캐시 (_TRANSCRIPT_CACHE_DIR) — 서버 재시작 후에도 재사용
      3) yt-dlp 실제 다운로드 — 요청 간격 최소 1.5초 보장 (429 방지)
    """
    global _last_transcript_fetch

    # 1) 인메모리 캐시
    if video_id in _transcript_cache:
        return _transcript_cache[video_id][:max_chars]

    # 2) 파일 캐시
    cache_file = _TRANSCRIPT_CACHE_DIR / f"{video_id}.txt"
    if cache_file.exists():
        try:
            transcript = cache_file.read_text(encoding='utf-8')
            _transcript_cache[video_id] = transcript   # 인메모리에도 올림
            print(f"  [YouTubeFetcher] 자막 파일 캐시 히트: {video_id}")
            return transcript[:max_chars]
        except Exception:
            pass

    # 3) yt-dlp 다운로드 — 요청 간격 보장
    elapsed = time.time() - _last_transcript_fetch
    if elapsed < _TRANSCRIPT_INTERVAL:
        time.sleep(_TRANSCRIPT_INTERVAL - elapsed)

    try:
        import yt_dlp
        import tempfile, os

        _last_transcript_fetch = time.time()
        with tempfile.TemporaryDirectory() as tmp:
            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                'skip_download': True,
                'writeautomaticsub': True,
                'subtitleslangs': ['en', 'ko'],
                'subtitlesformat': 'vtt',
                'outtmpl': os.path.join(tmp, '%(id)s.%(ext)s'),
                'restrictfilenames': True,   # Windows 특수문자 파일명 오류([Errno 22]) 방지
                'retries': 2,
                'fragment_retries': 2,
                'http_headers': {
                    'User-Agent': (
                        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                        'AppleWebKit/537.36 (KHTML, like Gecko) '
                        'Chrome/124.0.0.0 Safari/537.36'
                    )
                },
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([f'https://www.youtube.com/watch?v={video_id}'])

            # vtt 파일 찾기
            vtt_files = [f for f in os.listdir(tmp) if f.endswith('.vtt')]
            if not vtt_files:
                _transcript_cache[video_id] = ""
                cache_file.write_text("", encoding='utf-8')   # 실패도 파일로 캐시
                return ""

            # 우선순위: en > ko
            chosen = next((f for f in vtt_files if '.en.' in f), vtt_files[0])
            raw = open(os.path.join(tmp, chosen), encoding='utf-8').read()

        # VTT 파싱: 타임코드 + 텍스트만 추출
        lines_out = []
        ts_re = re.compile(r'(\d{2}:\d{2}:\d{2}\.\d+) --> ')
        prev_text = ""
        cur_ts = ""
        for line in raw.split('\n'):
            m = ts_re.match(line)
            if m:
                cur_ts = m.group(1)[:8]   # HH:MM:SS
            elif line.strip() and not line.startswith('WEBVTT') and '<' not in line:
                text = line.strip()
                if text != prev_text and cur_ts:
                    lines_out.append(f"[{cur_ts}] {text}")
                    prev_text = text

        transcript = "\n".join(lines_out)
        _transcript_cache[video_id] = transcript          # 인메모리 캐시
        cache_file.write_text(transcript, encoding='utf-8')  # 파일 캐시
        print(f"  [YouTubeFetcher] 자막 저장 완료: {video_id} ({len(transcript)}자)")
        return transcript[:max_chars]

    except Exception as e:
        err_msg = str(e)
        print(f"  [YouTubeFetcher] 자막 추출 실패 ({video_id}): {err_msg[:80]}")
        # 429는 일시적 — 파일 캐시 저장 안 함 (다음 세션에 재시도 가능하게)
        is_rate_limit = "429" in err_msg or "Too Many" in err_msg
        _transcript_cache[video_id] = ""   # 인메모리에만 캐시 (재시도 방지)
        if not is_rate_limit:
            cache_file.write_text("", encoding='utf-8')
        return ""


def _hms_to_seconds(hms: str) -> int:
    """'HH:MM:SS' 또는 'MM:SS' → 초"""
    parts = hms.strip().split(':')
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    elif len(parts) == 2:
        return int(parts[0]) * 60 + int(parts[1])
    return 0


def _extract_keywords_from_slide(title: str, body: str) -> list[str]:
    """
    슬라이드 텍스트에서 검색용 키워드 추출.
    한국어 슬라이드 → 영어 키워드도 함께 생성 (한/영 양방향 매칭).
    """
    import re as _re
    # 숫자+단위 패턴 (40%, $20, 3배, 1000만 등)
    nums = _re.findall(r'\d+[%$만억]?|\d+\.\d+', title + " " + body)
    # 2글자 이상 명사 (한국어)
    ko_words = _re.findall(r'[가-힣]{2,}', title + " " + body)
    # 영문 단어 2글자 이상
    en_words = _re.findall(r'[A-Za-z]{3,}', title + " " + body)
    kws = nums + ko_words[:6] + en_words[:6]
    return [k for k in kws if len(k) >= 2][:15]


def _keyword_search_transcript(transcript: str, keywords: list[str]) -> int:
    """
    자막 텍스트에서 키워드가 가장 많이 등장하는 타임스탬프 반환.
    한국어 키워드도 영어 자막에서 부분 매칭 시도 (숫자는 공통).
    """
    ts_re = re.compile(r'^\[(\d{2}:\d{2}:\d{2})\]\s*(.+)$')
    # 60초 단위 윈도우로 집계
    window_scores: dict[int, float] = {}

    lines = transcript.split('\n')
    for line in lines:
        m = ts_re.match(line)
        if not m:
            continue
        ts_raw, text = m.group(1), m.group(2).lower()
        bucket = (_hms_to_seconds(ts_raw) // 60) * 60   # 60초 버킷

        score = 0.0
        for kw in keywords:
            kw_l = kw.lower()
            # 숫자는 완전 일치 가중
            if kw.isdigit() or '%' in kw or '$' in kw:
                if kw_l in text:
                    score += 3.0
            else:
                if kw_l in text:
                    score += 1.0
                # 부분 매칭 (앞 3자)
                elif len(kw_l) >= 3 and kw_l[:3] in text:
                    score += 0.3

        if score > 0:
            window_scores[bucket] = window_scores.get(bucket, 0) + score

    if not window_scores:
        return 0

    best_bucket = max(window_scores, key=lambda b: window_scores[b])
    best_score = window_scores[best_bucket]
    print(f"  [YouTubeFetcher] 키워드 자막 매칭 → {best_bucket}초 (점수 {best_score:.1f})")
    return best_bucket


def find_best_start_time(
    video_id: str,
    slide_title: str,
    slide_body: str,
    video_title: str = "",
) -> int:
    """
    슬라이드 내용이 실제로 언급되는 영상 구간의 시작 시간(초) 반환.

    전략: 자막(= 영상에서 말하는 내용 전부)을 먼저 다운로드해
    GPT가 슬라이드의 구체적 수치·사실이 실제 언급되는 구간을 찾음.
    자막 없으면 챕터/설명 타임스탬프 → 키워드 스캔 → 0초 순 fallback.
    """
    slide_content = f"제목: {slide_title}\n내용: {slide_body[:300]}"
    slide_keywords = _extract_keywords_from_slide(slide_title, slide_body)

    try:
        from langchain_openai import ChatOpenAI
        from src.config import OPENAI_API_KEY
        llm = ChatOpenAI(model="gpt-4o-mini", temperature=0, api_key=OPENAI_API_KEY)

        # ── 1순위: 자막 전체 GPT 분석 (영상 내용을 직접 읽어 가장 정확) ──
        print(f"  [YouTubeFetcher] '{video_id}' 자막 다운로드 중 (슬라이드 내용 매칭)...")
        transcript = _get_video_transcript(video_id)
        if transcript:
            prompt = (
                f"아래 유튜브 영상 자막을 읽고, 카드뉴스 슬라이드에서 언급된 내용이 "
                f"영상에서 실제로 나오는 구간의 시작 시간을 찾아주세요.\n\n"
                f"카드뉴스 슬라이드:\n{slide_content}\n"
                f"핵심 키워드: {', '.join(slide_keywords[:10])}\n\n"
                f"영상 제목: {video_title}\n\n"
                f"자막 (타임코드 포함):\n{transcript[:4000]}\n\n"
                f"지시사항:\n"
                f"- 슬라이드의 수치·기업명·사건이 실제로 언급되는 구간을 찾으세요\n"
                f"- 슬라이드가 한국어라도 영어 자막에서 동일한 의미의 내용을 찾으세요\n"
                f"- 채널 소개·광고·자기소개(인트로)는 절대 선택하지 마세요\n"
                f"- 핵심 내용이 나오는 가장 이른 타임코드를 선택하세요\n"
                f"- 관련 내용이 전혀 없으면 0을 출력하세요\n\n"
                f"시작 시간(초, 숫자만. 예: 142):"
            )
            result = llm.invoke(prompt)
            raw = result.content.strip().split()[0]
            digits = "".join(filter(str.isdigit, raw))
            if digits:
                start = max(0, int(digits))
                if start > 0:
                    print(f"  [YouTubeFetcher] 자막 GPT 분석 → {start}초 (슬라이드 내용 언급 구간)")
                    return start

            # GPT가 0 반환 → 키워드 스캔 보조
            kw_start = _keyword_search_transcript(transcript, slide_keywords)
            if kw_start > 0:
                print(f"  [YouTubeFetcher] 키워드 스캔 보조 → {kw_start}초")
                return kw_start
        else:
            print(f"  [YouTubeFetcher] '{video_id}' 자막 없음 → 챕터/설명 타임스탬프 시도")

        # ── 2순위: 챕터 / 설명 타임스탬프 (자막 없을 때 fallback) ──
        meta = _get_video_metadata(video_id)
        chapters = meta.get('chapters', [])
        description = meta.get('description', '')

        def _ask_gpt_timestamp(label: str, ts_text: str) -> int:
            prompt = (
                f"영상 구간 목록에서 아래 슬라이드 내용과 가장 관련있는 구간을 골라주세요.\n"
                f"슬라이드: {slide_content}\n"
                f"핵심 키워드: {', '.join(slide_keywords[:8])}\n\n"
                f"{label}:\n{ts_text}\n\n"
                f"가장 관련있는 구간의 시작 시간(초, 숫자만). 없으면 0:"
            )
            result = llm.invoke(prompt)
            raw = result.content.strip().split()[0]
            return max(0, int("".join(filter(str.isdigit, raw)) or "0"))

        if chapters:
            ts_text = "\n".join(f"[{c['start_time']}초] {c['title']}" for c in chapters)
            start = _ask_gpt_timestamp("챕터 목록", ts_text)
            if start > 0:
                print(f"  [YouTubeFetcher] 챕터 GPT 선택 → {start}초 ({len(chapters)}개 중)")
                return start

        desc_timestamps = _parse_description_timestamps(description)
        if desc_timestamps:
            ts_text = "\n".join(f"[{t['start_time']}초] {t['title']}" for t in desc_timestamps)
            start = _ask_gpt_timestamp("설명 타임스탬프", ts_text)
            if start > 0:
                print(f"  [YouTubeFetcher] 설명타임스탬프 GPT 선택 → {start}초 ({len(desc_timestamps)}개 중)")
                return start

    except Exception as e:
        print(f"  [YouTubeFetcher] 타임스탬프 분석 실패({e}), 처음부터 재생")

    print(f"  [YouTubeFetcher] '{video_id}' 관련 구간 없음 → 처음부터")
    return 0


def download_video_snippet(
    video_id: str,
    duration: int = 15,
    start_time: int = 0,
) -> Path | None:
    """유튜브 영상에서 특정 구간 다운로드 (기본: 처음 15초)"""
    # start_time을 파일명에 포함해 캐시 구분
    cache_key = f"{video_id}_t{start_time}_{duration}"
    out_path = _CACHE_DIR / f"{cache_key}_snippet.mp4"
    if out_path.exists():
        print(f"  [YouTubeFetcher] 캐시 사용: {cache_key}")
        return out_path

    try:
        import yt_dlp
        import imageio_ffmpeg

        ffmpeg_bin = imageio_ffmpeg.get_ffmpeg_exe()
        end_time = start_time + duration

        def download_range_func(info_dict, ydl):
            return [{'start_time': start_time, 'end_time': end_time}]

        ydl_opts = {
            'format': 'bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]/best',
            'outtmpl': str(out_path),
            'ffmpeg_location': ffmpeg_bin,
            'download_ranges': download_range_func,
            'quiet': True,
            'no_warnings': True,
        }
        label = f"{start_time}초~{end_time}초"
        print(f"  [YouTubeFetcher] 영상 '{video_id}' {label} 다운로드 중...")
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([f'https://www.youtube.com/watch?v={video_id}'])

        if out_path.exists():
            return out_path
    except Exception as e:
        print(f"  [YouTubeFetcher] 영상 다운로드 실패: {e}")
    return None



def _get_creator_name(video_id: str) -> str:
    """YouTube oembed API로 채널명 가져오기"""
    try:
        r = httpx.get(
            "https://www.youtube.com/oembed",
            params={"url": f"https://www.youtube.com/watch?v={video_id}", "format": "json"},
            timeout=8,
        )
        if r.status_code == 200:
            return r.json().get("author_name", "")
    except Exception:
        pass
    return ""


def _generate_search_keywords(article_title: str, topic: str, n: int = 4) -> list[str]:
    """GPT-4o-mini로 기사 기반 다각도 유튜브 검색 키워드 생성 (영어 + 한국어 혼합)"""
    year = datetime.now().year
    n_en = max(1, n // 2)    # 영어 키워드 수
    n_ko = n - n_en          # 한국어 키워드 수
    prompt = (
        f"다음 기사를 커버하는 유튜브 영상을 찾기 위한 검색 키워드 {n}개를 만들어주세요.\n\n"
        f"기사 제목: {article_title}\n"
        f"주제: {topic}\n\n"
        f"규칙:\n"
        f"- 영어 {n_en}개 + 한국어 {n_ko}개 혼합 (총 {n}개)\n"
        f"- 각 키워드는 서로 다른 각도 (공식 발표, 실제 데모, 튜토리얼, 비교/리뷰)\n"
        f"- 영어: 10~25자 이내, 연도({year}) 또는 구체적 제품명 포함\n"
        f"- 한국어: 한국 유튜버가 쓸 법한 검색어, 15자 이내\n"
        f"- generic 금지 ('AI tutorial' 보다 'GPT-4o demo {year}' 수준으로)\n"
        f"JSON 배열만 출력: [\"en_kw1\", \"en_kw2\", \"한국어kw1\", \"한국어kw2\"]"
    )
    try:
        from langchain_openai import ChatOpenAI
        from src.config import OPENAI_API_KEY
        llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.3, api_key=OPENAI_API_KEY)
        result = llm.invoke(prompt)
        raw = re.sub(r"```[a-z]*\n?", "", result.content.strip()).strip("`")
        kws = json.loads(raw)
        keywords = [str(k) for k in kws[:n]]
        print(f"  [YouTubeFetcher] 생성된 키워드: {keywords}")
        return keywords
    except Exception as e:
        print(f"  [YouTubeFetcher] 키워드 생성 실패({e}), 기본값 사용")
        return [
            f"{topic} demo {year}",
            f"{topic} tutorial {year}",
            f"{topic} 사용법",
            f"{topic} 리뷰",
        ][:n]


def _search_youtube(keyword: str, n: int = 3, days: int = 30) -> list[VideoInfo]:
    """단일 키워드로 YouTube 검색 → VideoInfo 리스트"""
    if not TAVILY_API_KEY:
        return []
    try:
        from tavily import TavilyClient
        client = TavilyClient(api_key=TAVILY_API_KEY)
        results = client.search(
            f"site:youtube.com {keyword}",
            search_depth="basic",
            max_results=n * 2,
            days=days,
        )
        videos: list[VideoInfo] = []
        seen_ids: set[str] = set()
        for r in results.get("results", []):
            url = r.get("url", "")
            video_id = _extract_video_id(url)
            if not video_id or video_id in seen_ids:
                continue
            seen_ids.add(video_id)
            thumb = _download_thumbnail(video_id)
            creator = _get_creator_name(video_id)
            videos.append(VideoInfo(
                video_id=video_id,
                url=f"youtu.be/{video_id}",
                title=r.get("title", ""),
                creator=creator,
                snippet=r.get("content", "")[:300],
                thumbnail=thumb,
            ))
            if len(videos) >= n:
                break
        return videos
    except Exception as e:
        print(f"  [YouTubeFetcher] 검색 실패 ({keyword[:30]}): {e}")
        return []


def fetch_video_candidates(
    article_title: str,
    topic: str,
    n_keywords: int = 4,
    n_per: int = 3,
    days: int = 30,
    min_views: int = 10000,    # 최소 조회수 필터
    max_duration: int = 2700,  # 최대 영상 길이 45분
) -> list[VideoInfo]:
    """
    기사 제목 기반 다각도 키워드 생성 → 키워드당 n_per개 검색.
    중복 제거 → 병렬 유효성 검증 (비공개/삭제 영상 제거, 조회수 필터) → 조회수 정렬.
    """
    keywords = _generate_search_keywords(article_title, topic, n_keywords)
    seen_ids: set[str] = set()
    all_candidates: list[VideoInfo] = []

    for kw in keywords:
        results = _search_youtube(kw, n=n_per, days=days)
        for v in results:
            if v.video_id not in seen_ids:
                seen_ids.add(v.video_id)
                all_candidates.append(v)

    print(f"  [YouTubeFetcher] 총 {len(all_candidates)}개 수집 → 유효성 검증 중 (조회수 ≥{min_views:,})...")
    valid = _validate_candidates(
        all_candidates,
        min_views=min_views,
        max_duration=max_duration,
        max_workers=4,
        limit=len(all_candidates),   # 전체 검증
    )
    print(f"  [YouTubeFetcher] 유효 {len(valid)}개 / 제외 {len(all_candidates)-len(valid)}개 (조회수 내림차순 정렬)")
    return valid


def match_videos_to_slides(slides: list, candidates: list[VideoInfo]) -> list[VideoInfo | None]:
    """
    GPT가 각 content 슬라이드에 후보 영상 중 최적 영상을 1:1 매핑.
    반환: content 슬라이드 순서대로 [VideoInfo | None] (None = 영상 없음)
    """
    content_slides = [s for s in slides if s.slide_type == "content"]
    if not content_slides:
        return []
    if not candidates:
        return [None] * len(content_slides)

    slides_text = "\n".join(
        f"슬라이드{i+1}: {s.title} — {s.body[:100]}"
        for i, s in enumerate(content_slides)
    )
    cands_text = "\n".join(
        f"영상{i+1}: {v.title} | {v.creator or '?'} | {v.snippet[:80]}"
        for i, v in enumerate(candidates)
    )

    prompt = (
        f"각 슬라이드에 가장 관련있는 유튜브 영상을 배정해주세요.\n\n"
        f"슬라이드 ({len(content_slides)}개):\n{slides_text}\n\n"
        f"후보 영상 ({len(candidates)}개):\n{cands_text}\n\n"
        f"엄격한 관련성 기준:\n"
        f"- 슬라이드와 영상이 동일한 제품/서비스/기업/사건을 다뤄야 함\n"
        f"- 주제가 조금이라도 다르면 반드시 0 출력 (억지 매핑 절대 금지)\n"
        f"- 예: 슬라이드='GitHub Codex' → 'Automation Anywhere' 영상은 0\n"
        f"- 예: 슬라이드='GPT-4o 출시' → 'OpenAI GPT-4o demo' 영상만 허용\n"
        f"- 같은 영상을 여러 슬라이드에 써도 됨\n"
        f"- 슬라이드 수({len(content_slides)})와 동일한 개수로 출력\n"
        f"JSON 배열만 출력 예시: [2, 0, 3, 0]  ← 0=관련 영상 없음"
    )

    try:
        from langchain_openai import ChatOpenAI
        from src.config import OPENAI_API_KEY
        llm = ChatOpenAI(model="gpt-4o-mini", temperature=0, api_key=OPENAI_API_KEY)
        result = llm.invoke(prompt)
        raw = re.sub(r"```[a-z]*\n?", "", result.content.strip()).strip("`")
        assignments = json.loads(raw)

        matched: list[VideoInfo | None] = []
        for idx in assignments[:len(content_slides)]:
            idx = int(idx)
            if idx <= 0 or idx > len(candidates):
                matched.append(None)
            else:
                matched.append(candidates[idx - 1])
        while len(matched) < len(content_slides):
            matched.append(None)

        mapped = [v.title[:25] if v else "None" for v in matched]
        print(f"  [YouTubeFetcher] 매핑 결과: {mapped}")
        return matched

    except Exception as e:
        print(f"  [YouTubeFetcher] 매핑 실패({e}), 순서대로 배정")
        return [candidates[i] if i < len(candidates) else None
                for i in range(len(content_slides))]


def find_verified_video_for_slide(
    slide_title: str,
    slide_body: str,
    topic: str,
    candidates: list[VideoInfo] | None = None,
    max_verify: int = 4,
    search_days: int = 60,
    used_video_ids: set[str] | None = None,
    article_title: str = "",   # ★ 기사 제목 — 영상 매칭 필수 엔티티 기준
) -> tuple[VideoInfo | None, int]:
    """
    슬라이드 내용을 자막에서 직접 읽어 실제로 다루는 영상과 시작 시간(초) 반환.
    자막이 없는 영상은 제목/스니펫 키워드 매칭으로 대체.

    순서:
    1. 기존 후보 중 GPT 제목 필터 → 자막 다운로드 → 내용 일치 확인
    2. 없으면 슬라이드 키워드로 신규 검색 → 동일 과정
    3. 없으면 (None, 0)
    """
    try:
        from langchain_openai import ChatOpenAI
        from src.config import OPENAI_API_KEY
        llm = ChatOpenAI(model="gpt-4o-mini", temperature=0, api_key=OPENAI_API_KEY)
    except Exception as e:
        print(f"  [YouTubeFetcher] LLM 초기화 실패: {e}")
        return None, 0

    slide_content = f"제목: {slide_title}\n내용: {slide_body[:250]}"
    slide_keywords = _extract_keywords_from_slide(slide_title, slide_body)
    _used = used_video_ids or set()

    # ── 기사 제목에서 필수 엔티티 추출 ──────────────────────────────
    # 슬라이드 키워드("Cloud", "Agent")는 너무 일반적 → 기사 제목 엔티티("Cloudflare", "OpenAI")를 사용.
    # 영상 제목/자막에 이 엔티티 중 1개 이상이 반드시 있어야 통과.
    import re as _re_ent
    _src_for_entity = article_title or topic   # 기사 제목+본문 앞부분 or topic
    _kr_stopwords = {"이런", "그냥", "그리고", "그러나", "때문에", "통해서", "위해서", "대해서", "가장", "매우"}
    # 영어 일반 단어 — 브랜드/제품명이 아닌 것들 (대문자여도 엔티티 X)
    _en_stopwords = {
        "this", "that", "with", "from", "your", "their",
        "have", "been", "will", "they", "more", "some",
        "using", "what", "also", "into", "over",
        # 튜토리얼/기사 제목에 자주 나오는 일반 단어
        "how", "why", "when", "where", "who", "the", "and",
        "for", "are", "was", "not", "can", "new", "top",
        "best", "real", "free", "just", "all", "one", "two",
        "our", "its", "did", "get", "use", "let", "help",
        "make", "build", "create", "find", "give", "take",
        "here", "about", "after", "before", "first", "last",
        "next", "most", "many", "much", "very", "well",
        "ground", "based", "level", "type", "data", "model",
        "korean", "english", "chinese", "japanese", "global",
        "agent", "agents", "system", "platform", "solution",
        "demo", "tutorial", "guide", "review", "update",
        "part", "step", "time", "year", "month", "week",
    }
    _required_entities: list[str] = list(dict.fromkeys(
        w for w in _re_ent.findall(r'[A-Za-z][A-Za-z0-9\-\.]{2,}|[가-힣]{2,}', _src_for_entity)
        if ((w[0].isalpha() and w[0].isupper()) or ('가' <= w[0] <= '힣'))
        and w.lower() not in _en_stopwords
        and w not in _kr_stopwords
    ))
    if _required_entities:
        print(f"  [YouTubeFetcher] 필수 엔티티 ({_src_for_entity[:60]}): {_required_entities[:5]}")

    # 한국어 엔티티 비율이 과반이면 엔티티 게이트 비활성화
    # (한국 기사 엔티티는 영어 YouTube에서 매칭 불가 → 모든 영상 탈락하는 문제 방지)
    _kr_entity_ratio = sum(1 for e in _required_entities if any('가' <= c <= '힣' for c in e)) / max(len(_required_entities), 1)
    _entity_gate_active = _kr_entity_ratio < 0.5 and bool(_required_entities)
    if not _entity_gate_active and _required_entities:
        print(f"  [YouTubeFetcher] 엔티티 게이트 비활성화 (한국어 엔티티 {_kr_entity_ratio:.0%} → 영어 YouTube 매칭 불가)")

    def _entity_gate(title: str, text: str, label: str) -> bool:
        """필수 엔티티 중 1개 이상이 title 또는 text에 있어야 통과"""
        if not _entity_gate_active:
            return True   # 게이트 비활성화 시 전부 통과
        t_lower = title.lower()
        x_lower = text.lower()
        for ent in _required_entities:
            el = ent.lower()
            if el in t_lower or el in x_lower:
                return True
        print(f"  [YouTubeFetcher] ✗ 엔티티 미매칭({label}): '{title[:40]}' ∌ {_required_entities[:3]}")
        return False

    def _verify_via_transcript(vi: VideoInfo) -> tuple[bool, int]:
        """자막 다운로드(캐시 우선) → GPT로 슬라이드 내용 포함 여부 + 타임스탬프 확인"""
        if vi.video_id in _used:
            return False, 0   # 이미 다른 슬라이드에 배정된 영상 제외

        transcript = _get_video_transcript(vi.video_id)

        if not transcript:
            # 자막 없음 → 제목+스니펫으로 엔티티 게이트 + 키워드 매칭
            combined = f"{vi.title} {vi.snippet}".lower()
            if not _entity_gate(vi.title, vi.snippet, "자막없음"):
                return False, 0

            core_kws = [k for k in slide_keywords if any(c.isdigit() for c in k) or k.isascii()][:8]
            hits = sum(1 for k in core_kws if k.lower() in combined)
            matched = hits >= 2
            print(f"  [YouTubeFetcher] {'✓' if matched else '✗'} 자막없음·키워드매칭({hits}/{len(core_kws)}): '{vi.title[:40]}'")
            return matched, 0

        # 자막이 있어도 엔티티 게이트: 기사 핵심 기업/제품명이 자막에 없으면 즉시 탈락
        # (hyperbrowser가 "cloud"를 언급해도 "Cloudflare"가 없으면 탈락)
        if not _entity_gate(vi.title, transcript[:3000], "자막있음"):
            return False, 0

        _entity_str = ', '.join(_required_entities[:5]) if _required_entities else topic
        prompt = (
            f"유튜브 영상 자막을 읽고 아래 슬라이드 내용을 실제로 다루는지 판단하세요.\n\n"
            f"필수 엔티티: {_entity_str}\n"
            f"슬라이드:\n{slide_content}\n\n"
            f"영상 제목: {vi.title}\n"
            f"영상 자막:\n{transcript[:4000]}\n\n"
            f"판단 기준 (아래 조건 중 하나라도 위반하면 match=false):\n"
            f"1. 필수 엔티티가 슬라이드와 같은 맥락으로 자막에 등장해야 함\n"
            f"2. 슬라이드 주제가 영상의 핵심 내용이어야 함 (스쳐 지나가는 언급 X)\n"
            f"3. ❌ 개념 혼동 금지 예시:\n"
            f"   - 슬라이드='Cloudflare Workers AI 실시간 배포' → 'AI 학습 데이터 필요성'는 match=false\n"
            f"   - 슬라이드='OpenAI 모델 가격' → 'AGI 철학적 토론'은 match=false\n"
            f"   - 슬라이드='Cloudflare Sandbox 마이크로 VM' → 'AWS Lambda vs 서비스리스'는 match=false\n"
            f"4. 인트로/광고/자기소개 구간 제외\n"
            f"5. 슬라이드가 한국어라도 영어 자막에서 동일 의미를 찾으세요\n\n"
            f"JSON만 출력 (다른 텍스트 금지):\n"
            f"{{\"match\": true/false, \"start_seconds\": 관련내용 첫 등장 초(정수, 없으면 0), "
            f"\"reason\": \"한 줄 근거\"}}"
        )
        try:
            result = llm.invoke(prompt)
            raw = re.sub(r"```[a-z]*\n?", "", result.content.strip()).strip("`").strip()
            # JSON 블록만 추출
            m = re.search(r'\{.*\}', raw, re.DOTALL)
            if m:
                raw = m.group(0)
            data = json.loads(raw)
            matched  = bool(data.get("match", False))
            start    = max(0, int(data.get("start_seconds", 0) or 0))
            reason   = str(data.get("reason", ""))[:60]
            mark = "✓ 매칭" if matched else "✗ 불일치"
            print(f"  [YouTubeFetcher] {mark}: '{vi.title[:35]}' → {reason} (→{start}초)")
            return matched, start
        except Exception as e:
            print(f"  [YouTubeFetcher] 자막 판단 실패({e}): '{vi.title[:30]}'")
            return False, 0

    # ── 1. 기존 후보 풀에서 슬라이드별 필터 후 자막 검증 ──────────
    if candidates:
        # 제목/스니펫으로 1차 필터 (GPT) → max_verify개만 자막 다운로드
        cands_text = "\n".join(
            f"{i+1}: {v.title} | {v.snippet[:70]}"
            for i, v in enumerate(candidates[:12])
        )
        filter_prompt = (
            f"슬라이드 내용과 관련 있을 가능성이 있는 영상 번호를 골라주세요.\n"
            f"슬라이드: {slide_content}\n\n"
            f"영상 목록:\n{cands_text}\n\n"
            f"관련 가능성 있는 번호 최대 3개. 없으면 []. JSON 배열만: [1, 3]"
        )
        try:
            res = llm.invoke(filter_prompt)
            raw_f = re.sub(r"```[a-z]*\n?", "", res.content.strip()).strip("`")
            m2 = re.search(r'\[.*\]', raw_f, re.DOTALL)
            indices = []
            if m2:
                for x in json.loads(m2.group(0)):
                    try:
                        idx = int(x) - 1
                        if 0 <= idx < len(candidates):
                            indices.append(idx)
                    except Exception:
                        pass
            top_from_pool = [candidates[i] for i in indices]
        except Exception:
            top_from_pool = candidates[:3]

        for vi in top_from_pool[:max_verify]:
            matched, start = _verify_via_transcript(vi)
            if matched:
                return vi, start

    # ── 2. 슬라이드 전용 신규 검색 ────────────────────────────
    print(f"  [YouTubeFetcher] '{slide_title[:20]}' 전용 검색 시작...")
    search_input = f"{slide_title} {slide_body[:80]} {topic}"
    kws = _generate_search_keywords(search_input, topic, n=3)

    new_cands: list[VideoInfo] = []
    seen_ids: set[str] = {v.video_id for v in (candidates or [])}
    for kw in kws:
        for v in _search_youtube(kw, n=4, days=search_days):
            if v.video_id not in seen_ids:
                seen_ids.add(v.video_id)
                new_cands.append(v)

    if new_cands:
        new_cands = _validate_candidates(new_cands, min_views=2000, max_workers=3, limit=8)

    for vi in new_cands[:max_verify]:
        matched, start = _verify_via_transcript(vi)
        if matched:
            return vi, start

    # ── 3. Fallback 제거 ──
    # 완벽하게 일치하지 않는 영상(단순 관련 엔티티 영상)은 사용자 경험을 해치므로 반환하지 않음.

    print(f"  [YouTubeFetcher] '{slide_title[:20]}' 영상 완전 없음 → 이미지 슬라이드")
    return None, 0


def fetch_video_details(keyword: str, n: int = 4) -> list[VideoInfo]:
    """
    키워드로 유튜브 영상 검색 + 썸네일 + 제목 + 채널명 + 설명 스니펫 반환.
    카드 4-5의 '실제 사용 예시' 콘텐츠에 사용.
    """
    print(f"  [YouTubeFetcher] '{keyword}' 영상 {n}개 상세 수집 중...")
    if not TAVILY_API_KEY:
        return []
    try:
        from tavily import TavilyClient
        client = TavilyClient(api_key=TAVILY_API_KEY)
        results = client.search(
            f"site:youtube.com {keyword}",
            search_depth="basic",
            max_results=n * 2,
            days=90,
        )
        videos: list[VideoInfo] = []
        seen_ids: set[str] = set()
        for r in results.get("results", []):
            url = r.get("url", "")
            video_id = _extract_video_id(url)
            if not video_id or video_id in seen_ids:
                continue
            seen_ids.add(video_id)

            title   = r.get("title", "")
            snippet = r.get("content", "")[:300]
            thumb   = _download_thumbnail(video_id)
            creator = _get_creator_name(video_id)

            short_url = f"youtu.be/{video_id}"
            info = VideoInfo(
                video_id=video_id,
                url=short_url,
                title=title,
                creator=creator,
                snippet=snippet,
                thumbnail=thumb,
            )
            videos.append(info)
            print(f"  [YouTubeFetcher] [{len(videos)}/{n}] '{title[:45]}' by {creator}")
            if len(videos) >= n:
                break
        return videos
    except Exception as e:
        print(f"  [YouTubeFetcher] 영상 상세 수집 실패: {e}")
        return []


def fetch_multiple_thumbnails(keyword: str, n: int = 4) -> list[Image.Image]:
    """
    키워드로 유튜브 썸네일 여러 개 수집 (content 슬라이드 수만큼).
    각 content 슬라이드에 다른 데모 영상 썸네일을 붙이기 위해 사용.
    """
    print(f"  [YouTubeFetcher] '{keyword}' 썸네일 {n}개 검색 중...")
    if not TAVILY_API_KEY:
        return []
    try:
        from tavily import TavilyClient
        client = TavilyClient(api_key=TAVILY_API_KEY)
        results = client.search(
            f"site:youtube.com {keyword}",
            search_depth="basic",
            max_results=n * 2,
            days=90,
        )
        thumbnails: list[Image.Image] = []
        seen_ids: set[str] = set()
        for r in results.get("results", []):
            url = r.get("url", "")
            video_id = _extract_video_id(url)
            if not video_id or video_id in seen_ids:
                continue
            img = _download_thumbnail(video_id)
            if img:
                thumbnails.append(img)
                seen_ids.add(video_id)
                print(f"  [YouTubeFetcher] [{len(thumbnails)}/{n}] {video_id} {img.size}")
            if len(thumbnails) >= n:
                break
        return thumbnails
    except Exception as e:
        print(f"  [YouTubeFetcher] 다중 썸네일 실패: {e}")
        return []


def fetch_thumbnail(keyword: str) -> Image.Image | None:
    """
    키워드로 유튜브 썸네일 검색 + 다운로드.

    Args:
        keyword: 검색 키워드 (예: "AI 에이전트 유튜브")
    Returns:
        PIL Image 또는 None (실패 시)
    """
    print(f"  [YouTubeFetcher] '{keyword}' 썸네일 검색 중...")

    yt_url = _search_youtube_url_via_tavily(keyword)
    if not yt_url:
        print("  [YouTubeFetcher] 유튜브 URL 없음")
        return None

    video_id = _extract_video_id(yt_url)
    if not video_id:
        print(f"  [YouTubeFetcher] video ID 추출 실패: {yt_url}")
        return None

    print(f"  [YouTubeFetcher] video ID: {video_id}")
    img = _download_thumbnail(video_id)
    if img:
        print(f"  [YouTubeFetcher] 썸네일 다운로드 성공: {img.size}")
    else:
        print("  [YouTubeFetcher] 썸네일 다운로드 실패")
    return img
