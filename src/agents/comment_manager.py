"""
CommentManager — Instagram 댓글 자동 AI 답글
────────────────────────────────────────────────────────
1. 최근 게시물의 댓글 조회
2. 아직 답글하지 않은 댓글 필터링
3. GPT-4o-mini가 브랜드 톤에 맞는 답글 생성
4. Instagram Graph API로 자동 답글 게시
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate

from src.config import OPENAI_API_KEY
from src.persona import load_persona

GRAPH_BASE = "https://graph.facebook.com/v19.0"

# 환경 변수 로드
from dotenv import load_dotenv
import os
load_dotenv()
IG_ACCESS_TOKEN = os.getenv("IG_ACCESS_TOKEN", "")
IG_USER_ID      = os.getenv("IG_USER_ID", "")

# 이미 답글한 댓글 ID 추적 (중복 방지)
_REPLIED_CACHE = Path("data/replied_comments.json")


def _load_replied() -> set[str]:
    if _REPLIED_CACHE.exists():
        return set(json.loads(_REPLIED_CACHE.read_text(encoding="utf-8")))
    return set()


def _save_replied(replied: set[str]) -> None:
    _REPLIED_CACHE.parent.mkdir(parents=True, exist_ok=True)
    _REPLIED_CACHE.write_text(
        json.dumps(list(replied), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ── Instagram API 호출 ────────────────────────────────────

def _get(path: str, **params) -> dict:
    params["access_token"] = IG_ACCESS_TOKEN
    r = httpx.get(f"{GRAPH_BASE}/{path}", params=params, timeout=15)
    return r.json()


def _post(path: str, **data) -> dict:
    data["access_token"] = IG_ACCESS_TOKEN
    r = httpx.post(f"{GRAPH_BASE}/{path}", data=data, timeout=15)
    return r.json()


def _get_recent_media(limit: int = 5) -> list[dict]:
    """최근 게시물 목록 조회."""
    data = _get(f"{IG_USER_ID}/media", fields="id,caption,timestamp", limit=limit)
    return data.get("data", [])


def _get_comments(media_id: str) -> list[dict]:
    """게시물의 댓글 조회."""
    data = _get(
        f"{media_id}/comments",
        fields="id,text,username,timestamp,replies{id,username}",
        limit=50,
    )
    return data.get("data", [])


def _reply_to_comment(comment_id: str, text: str) -> bool:
    """댓글에 답글 달기."""
    result = _post(f"{comment_id}/replies", message=text)
    return "id" in result


# ── GPT-4o-mini 답글 생성 ─────────────────────────────────

_SYSTEM = """
당신은 인스타그램 카드뉴스 계정 '{brand_name}'({handle})의 소셜 매니저입니다.
팔로워의 댓글에 답글을 작성합니다.

답글 스타일: {reply_tone}

규칙:
- 반드시 한국어로 작성
- 1~2문장 이내로 짧게
- 상대방 @멘션 포함 (예: @username 감사해요!)
- 핵심 정보나 인사이트를 추가해 가치 있게
- 이모지 1개 자연스럽게 포함
- 스팸이나 광고성 댓글엔 간단히 인사만
"""

_HUMAN = """
게시물 내용 요약: {post_context}

댓글 작성자: @{username}
댓글 내용: {comment_text}

위 댓글에 달 답글을 작성해주세요.
"""


@dataclass
class CommentReply:
    comment_id: str
    username: str
    original: str
    reply: str


def _generate_reply(
    username: str,
    comment_text: str,
    post_context: str,
    persona,
) -> str:
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.7, api_key=OPENAI_API_KEY)
    prompt = ChatPromptTemplate.from_messages([
        ("system", _SYSTEM),
        ("human",  _HUMAN),
    ])
    chain = prompt | llm
    result = chain.invoke({
        "brand_name":  persona.brand_name,
        "handle":      persona.handle,
        "reply_tone":  getattr(persona, "comment_reply_tone",
                               "친근하고 유익하게, 짧고 핵심 정보 추가"),
        "post_context": post_context,
        "username":    username,
        "comment_text": comment_text,
    })
    return result.content.strip()


# ── 공개 API ──────────────────────────────────────────────

def run(dry_run: bool = False) -> list[CommentReply]:
    """
    최근 게시물 댓글 확인 → AI 답글 게시.
    dry_run=True 이면 실제 게시 없이 생성된 답글만 반환.
    """
    if not IG_ACCESS_TOKEN or not IG_USER_ID:
        print("  [CommentManager] IG_ACCESS_TOKEN 또는 IG_USER_ID 없음. 스킵.")
        return []

    persona      = load_persona()
    replied_ids  = _load_replied()
    results: list[CommentReply] = []

    media_list = _get_recent_media(limit=5)
    if not media_list:
        print("  [CommentManager] 최근 게시물 없음.")
        return []

    print(f"  [CommentManager] 게시물 {len(media_list)}개 댓글 확인 중...")

    for media in media_list:
        media_id      = media["id"]
        post_context  = (media.get("caption") or "")[:200]
        comments      = _get_comments(media_id)

        for comment in comments:
            cid      = comment["id"]
            username = comment.get("username", "user")
            text     = comment.get("text", "")

            # 이미 답글한 댓글 스킵
            if cid in replied_ids:
                continue

            # 자신의 계정 댓글 스킵
            if username == persona.handle.lstrip("@"):
                replied_ids.add(cid)
                continue

            # 빈 댓글 스킵
            if not text.strip():
                continue

            print(f"  [CommentManager] @{username}: {text[:50]}...")
            reply_text = _generate_reply(username, text, post_context, persona)

            if not dry_run:
                success = _reply_to_comment(cid, reply_text)
                if success:
                    print(f"    → 답글 완료: {reply_text[:60]}...")
                    replied_ids.add(cid)
                else:
                    print(f"    → 답글 실패")
            else:
                print(f"    → [DRY RUN] {reply_text[:60]}...")
                replied_ids.add(cid)

            results.append(CommentReply(
                comment_id=cid,
                username=username,
                original=text,
                reply=reply_text,
            ))

    _save_replied(replied_ids)
    print(f"  [CommentManager] 처리 완료: {len(results)}개 답글")
    return results


if __name__ == "__main__":
    replies = run(dry_run=True)
    for r in replies:
        print(f"\n@{r.username}: {r.original}")
        print(f"→ {r.reply}")
