"""
Instagram 장기 토큰 자동 갱신 스크립트.
만료까지 15일 이하 남으면 갱신하고 .env 파일을 업데이트.
Windows 작업 스케줄러에 매주 실행 등록 권장.
"""
from __future__ import annotations

import io
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env")

IG_ACCESS_TOKEN = os.getenv("IG_ACCESS_TOKEN", "")
GRAPH_BASE = "https://graph.instagram.com/v21.0"
REFRESH_THRESHOLD_DAYS = 15


def _log(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def get_token_expiry(token: str) -> int | None:
    """Instagram Business Login 토큰 만료 시각(unix) 반환. 실패 시 None."""
    resp = httpx.get(
        f"{GRAPH_BASE}/me",
        params={"fields": "id,username", "access_token": token},
        timeout=10,
    )
    data = resp.json()
    if "error" in data:
        _log(f"토큰 상태 확인 오류: {data['error'].get('message', data)}")
        return None
    # Instagram Business Login 토큰은 /me 응답에 만료일 없음
    # refresh 엔드포인트 응답의 expires_in으로 만료까지 남은 초 확인
    refresh_resp = httpx.get(
        f"{GRAPH_BASE}/refresh_access_token",
        params={"grant_type": "ig_refresh_token", "access_token": token},
        timeout=15,
    )
    rdata = refresh_resp.json()
    if "access_token" in rdata:
        expires_in = rdata.get("expires_in", 0)
        days_left = expires_in // 86400
        return days_left, rdata["access_token"]
    return None


def update_env_token(new_token: str) -> None:
    env_path = ROOT / ".env"
    content = env_path.read_text(encoding="utf-8")
    content = re.sub(
        r"^IG_ACCESS_TOKEN=.*$",
        f"IG_ACCESS_TOKEN={new_token}",
        content,
        flags=re.MULTILINE,
    )
    env_path.write_text(content, encoding="utf-8")
    _log(".env 토큰 업데이트 완료")


def main() -> None:
    if not IG_ACCESS_TOKEN:
        _log("IG_ACCESS_TOKEN 없음 -- 스킵")
        sys.exit(0)

    _log("Instagram 토큰 상태 확인 중...")

    result = get_token_expiry(IG_ACCESS_TOKEN)
    if result is None:
        _log("만료일 확인 불가 -- 현재 토큰 유지")
        sys.exit(0)

    days_left, new_token = result
    _log(f"토큰 만료까지 {days_left}일 남음")

    if new_token != IG_ACCESS_TOKEN:
        _log("갱신된 토큰 수신 -- .env 업데이트")
        update_env_token(new_token)
        _log(f"갱신 완료! 새 토큰 만료까지 {days_left}일")
    else:
        _log("갱신 불필요 (토큰 유효)")


if __name__ == "__main__":
    main()
