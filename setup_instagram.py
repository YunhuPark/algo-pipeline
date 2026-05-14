"""
Instagram Graph API 일회성 셋업 스크립트
─────────────────────────────────────────────────────────────────────
수동으로 해야 할 일 (딱 3가지):

  STEP 1. Meta 개발자 앱 만들기
    → https://developers.facebook.com/apps/creation
    → '비즈니스' 유형 선택 → 앱 만들기
    → 왼쪽 '제품 추가' → Instagram Graph API 추가
    → [앱 설정 > 기본 설정]에서 앱 ID, 앱 시크릿 코드 복사

  STEP 2. Instagram 비즈니스 계정 연결
    → 인스타그램 앱: 설정 → 계정 → 프로페셔널 계정으로 전환(비즈니스/크리에이터)
    → Facebook 페이지 없으면 생성: https://www.facebook.com/pages/create
    → 인스타 설정 → 크리에이터/비즈니스 도구 → '연결된 Facebook 페이지' 에서 연결

  STEP 3. 단기 액세스 토큰 발급 (유효기간 1시간, 이 스크립트가 60일 토큰으로 변환)
    → https://developers.facebook.com/tools/explorer
    → 상단에서 본인 앱 선택
    → 권한 추가: instagram_basic / instagram_content_publish / pages_read_engagement
    → '액세스 토큰 생성' 클릭 → 토큰 복사

이 스크립트가 자동으로 처리하는 것:
  ✓ 단기 토큰 → 장기 토큰(60일) 변환
  ✓ 연결된 Facebook 페이지 목록 조회
  ✓ Instagram 비즈니스 계정 ID(IG_USER_ID) 자동 발견
  ✓ .env 파일 자동 업데이트
  ✓ 설정 검증 테스트
─────────────────────────────────────────────────────────────────────
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import httpx

GRAPH_BASE = "https://graph.facebook.com/v19.0"
ENV_PATH = Path(__file__).parent / ".env"


# ── 유틸 ──────────────────────────────────────────────────

def _get(url: str, **params) -> dict:
    r = httpx.get(url, params=params, timeout=15)
    return r.json()


def _update_env(key: str, value: str) -> None:
    """키=값 형태로 .env 파일에 쓰거나 덮어씁니다."""
    text = ENV_PATH.read_text(encoding="utf-8") if ENV_PATH.exists() else ""
    pattern = rf"^{re.escape(key)}=.*$"
    new_line = f"{key}={value}"
    if re.search(pattern, text, flags=re.MULTILINE):
        text = re.sub(pattern, new_line, text, flags=re.MULTILINE)
    else:
        text = text.rstrip("\n") + f"\n{new_line}\n"
    ENV_PATH.write_text(text, encoding="utf-8")
    print(f"  .env 업데이트: {key}=...{value[-6:]}")


# ── 핵심 단계 ──────────────────────────────────────────────

def step_long_lived_token(short_token: str, app_id: str, app_secret: str) -> str:
    print("\n[자동] 장기 액세스 토큰(60일) 변환 중...")
    data = _get(
        f"{GRAPH_BASE}/oauth/access_token",
        grant_type="fb_exchange_token",
        client_id=app_id,
        client_secret=app_secret,
        fb_exchange_token=short_token,
    )
    if "access_token" not in data:
        print(f"  오류: {data}")
        sys.exit(1)
    token = data["access_token"]
    expires = data.get("expires_in", "알 수 없음")
    print(f"  → 장기 토큰 발급 성공 (만료: {expires}초 후)")
    _update_env("IG_ACCESS_TOKEN", token)
    return token


def step_find_ig_user_id(token: str) -> str:
    print("\n[자동] 연결된 Facebook 페이지 및 Instagram 계정 검색 중...")

    # 연결된 FB 페이지 목록
    pages_data = _get(f"{GRAPH_BASE}/me/accounts", access_token=token)
    pages = pages_data.get("data", [])
    if not pages:
        print("  오류: 연결된 Facebook 페이지가 없습니다.")
        print("  Instagram을 Facebook 페이지와 연결하세요 (STEP 2 참고)")
        sys.exit(1)

    print(f"  연결된 Facebook 페이지 {len(pages)}개 발견:")
    candidates: list[tuple[str, str, str]] = []  # (page_name, page_id, ig_id)

    for page in pages:
        page_id = page["id"]
        page_name = page["name"]
        ig_data = _get(
            f"{GRAPH_BASE}/{page_id}",
            fields="instagram_business_account",
            access_token=token,
        )
        ig_account = ig_data.get("instagram_business_account")
        if ig_account:
            ig_id = ig_account["id"]
            candidates.append((page_name, page_id, ig_id))
            print(f"    ✓ {page_name} → Instagram ID: {ig_id}")
        else:
            print(f"    ✗ {page_name} → Instagram 연결 없음")

    if not candidates:
        print("\n  오류: Instagram 비즈니스 계정이 Facebook 페이지에 연결되지 않았습니다.")
        print("  인스타그램 설정 → 비즈니스 도구 → '연결된 Facebook 페이지'에서 연결하세요.")
        sys.exit(1)

    if len(candidates) == 1:
        page_name, _, ig_id = candidates[0]
        print(f"\n  → '{page_name}' 계정 자동 선택")
    else:
        print("\n  여러 계정이 발견됐습니다. 사용할 계정 번호를 입력하세요:")
        for i, (name, _, ig_id) in enumerate(candidates, 1):
            print(f"    [{i}] {name} (IG ID: {ig_id})")
        choice = int(input("  번호: ").strip()) - 1
        _, _, ig_id = candidates[choice]

    _update_env("IG_USER_ID", ig_id)
    return ig_id


def step_verify(token: str, ig_user_id: str) -> None:
    print("\n[자동] 설정 검증 중...")

    # 토큰 상태 확인
    debug = _get(
        f"{GRAPH_BASE}/debug_token",
        input_token=token,
        access_token=token,
    ).get("data", {})

    if not debug.get("is_valid"):
        print(f"  경고: 토큰이 유효하지 않습니다. 오류: {debug.get('error')}")
    else:
        import datetime
        exp = debug.get("expires_at", 0)
        if exp:
            exp_dt = datetime.datetime.fromtimestamp(exp).strftime("%Y-%m-%d")
            print(f"  ✓ 토큰 유효 (만료: {exp_dt})")
        else:
            print("  ✓ 토큰 유효 (만료 없음)")

    # IG 계정 정보 확인
    ig_info = _get(
        f"{GRAPH_BASE}/{ig_user_id}",
        fields="username,account_type",
        access_token=token,
    )
    if "username" in ig_info:
        print(f"  ✓ Instagram 계정: @{ig_info['username']} ({ig_info.get('account_type', '?')})")
    else:
        print(f"  경고: IG 계정 정보 조회 실패: {ig_info}")


# ── 메인 ──────────────────────────────────────────────────

def main() -> None:
    print(__doc__)
    print("=" * 65)
    print("STEP 1~3을 완료한 후 아래 정보를 입력하세요.")
    print("=" * 65)

    short_token = input("\n단기 액세스 토큰 (Graph API Explorer에서 복사): ").strip()
    app_id      = input("앱 ID                 (개발자 대시보드 > 앱 설정 > 기본 설정): ").strip()
    app_secret  = input("앱 시크릿 코드        (개발자 대시보드 > 앱 설정 > 기본 설정): ").strip()

    if not all([short_token, app_id, app_secret]):
        print("\n오류: 세 값 모두 입력해야 합니다.")
        sys.exit(1)

    long_token = step_long_lived_token(short_token, app_id, app_secret)

    # IG_USER_ID가 이미 .env에 있으면 페이지 탐색 건너뜀
    from dotenv import load_dotenv
    import os
    load_dotenv(ENV_PATH, override=True)
    existing_ig_id = os.getenv("IG_USER_ID", "").strip()
    if existing_ig_id:
        print(f"\n[자동] IG_USER_ID 기존 값 사용: {existing_ig_id}")
        ig_user_id = existing_ig_id
    else:
        ig_user_id = step_find_ig_user_id(long_token)

    step_verify(long_token, ig_user_id)

    print("\n" + "=" * 65)
    print("  셋업 완료! .env 파일이 자동으로 업데이트됐습니다.")
    print()
    print("  이제 카드뉴스 생성 + 인스타 자동 업로드:")
    print('  python main.py "AI 트렌드" --publish')
    print()
    print("  ※ ngrok이 없으면 업로드 시 자동 설치 & 터널 시작됩니다.")
    print("=" * 65)


if __name__ == "__main__":
    main()
