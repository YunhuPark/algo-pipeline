"""
DMManager — Instagram 수신 DM 자동 AI 답장
────────────────────────────────────────────────────────
Meta 정책: 먼저 DM을 보낸 사용자에게만 답장 가능.
           내가 먼저 DM을 보내는 것(콜드 DM)은 정책 위반.

필요 권한: instagram_manage_messages
필요 설정: Meta 앱 → Instagram → Messenger API 활성화

1. 미답장 대화 목록 조회
2. GPT-4o-mini가 브랜드 톤에 맞는 답장 생성
3. Instagram Graph API로 자동 답장 발송
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

from dotenv import load_dotenv
import os
load_dotenv()
IG_ACCESS_TOKEN = os.getenv("IG_ACCESS_TOKEN", "")
IG_USER_ID      = os.getenv("IG_USER_ID", "")

# 이미 답장한 대화 추적
_REPLIED_CACHE = Path("data/replied_dms.json")


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


# ── Instagram Messaging API ──────────────────────────────

def _get(path: str, **params) -> dict:
    params["access_token"] = IG_ACCESS_TOKEN
    r = httpx.get(f"{GRAPH_BASE}/{path}", params=params, timeout=15)
    return r.json()


def _post_json(path: str, payload: dict) -> dict:
    r = httpx.post(
        f"{GRAPH_BASE}/{path}",
        json=payload,
        headers={"Authorization": f"Bearer {IG_ACCESS_TOKEN}"},
        timeout=15,
    )
    return r.json()


def _get_conversations() -> list[dict]:
    """
    수신된 DM 대화 목록 조회.
    instagram_manage_messages 권한 필요.
    """
    data = _get(
        f"{IG_USER_ID}/conversations",
        platform="instagram",
        fields="id,updated_time,participants",
    )
    return data.get("data", [])


def _get_messages(conversation_id: str) -> list[dict]:
    """대화의 최근 메시지 조회."""
    data = _get(
        f"{conversation_id}/messages",
        fields="id,message,from,created_time",
        limit=5,
    )
    return data.get("data", [])


def _send_dm(recipient_id: str, text: str) -> bool:
    """DM 발송."""
    result = _post_json(
        f"{IG_USER_ID}/messages",
        {
            "recipient": {"id": recipient_id},
            "message": {"text": text},
        },
    )
    return "message_id" in result or "id" in result


# ── GPT-4o-mini 답장 생성 ─────────────────────────────────

_SYSTEM = """
당신은 인스타그램 카드뉴스 계정 '{brand_name}'({handle})의 DM 매니저입니다.
팔로워가 보낸 DM에 답장을 작성합니다.

답장 스타일: {dm_tone}

규칙:
- 반드시 한국어로 작성
- 2~3문장 이내
- 따뜻하고 개인적인 느낌
- 질문이 있으면 명확하게 답변
- 콘텐츠 관련 질문이면 추가 팁 제공
- 자연스럽게 팔로우/알림 설정 유도 (강요 X)
- 이모지 1~2개 자연스럽게 포함
"""

_HUMAN = """
받은 DM 내용: {message}

위 DM에 대한 답장을 작성해주세요.
"""


def _generate_reply(message: str, persona) -> str:
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.7, api_key=OPENAI_API_KEY)
    prompt = ChatPromptTemplate.from_messages([
        ("system", _SYSTEM),
        ("human",  _HUMAN),
    ])
    chain = prompt | llm
    result = chain.invoke({
        "brand_name": persona.brand_name,
        "handle":     persona.handle,
        "dm_tone":    getattr(persona, "dm_reply_tone",
                              "따뜻하고 개인적인 느낌으로 친절하게"),
        "message":    message,
    })
    return result.content.strip()


# ── 공개 API ──────────────────────────────────────────────

@dataclass
class DMReply:
    conversation_id: str
    sender_id: str
    original_message: str
    reply: str


def run(dry_run: bool = False) -> list[DMReply]:
    """
    미답장 DM 확인 → AI 답장 발송.
    dry_run=True 이면 실제 발송 없이 생성된 답장만 반환.

    ※ instagram_manage_messages 권한 없으면 자동으로 스킵됩니다.
    """
    if not IG_ACCESS_TOKEN or not IG_USER_ID:
        print("  [DMManager] IG_ACCESS_TOKEN 또는 IG_USER_ID 없음. 스킵.")
        return []

    persona     = load_persona()
    replied_ids = _load_replied()
    results: list[DMReply] = []

    conversations = _get_conversations()

    # 권한 없으면 error 반환됨
    if not isinstance(conversations, list):
        print("  [DMManager] 대화 목록 조회 실패. instagram_manage_messages 권한 확인 필요.")
        return []

    if not conversations:
        print("  [DMManager] 새 DM 없음.")
        return []

    print(f"  [DMManager] DM 대화 {len(conversations)}개 확인 중...")

    my_ig_id = IG_USER_ID

    for conv in conversations:
        conv_id = conv["id"]

        if conv_id in replied_ids:
            continue

        messages = _get_messages(conv_id)
        if not messages:
            continue

        # 가장 최근 메시지
        latest = messages[0]
        sender_id = latest.get("from", {}).get("id", "")
        text      = latest.get("message", "")

        # 내가 마지막으로 답장한 경우 스킵
        if sender_id == my_ig_id:
            replied_ids.add(conv_id)
            continue

        if not text.strip():
            continue

        print(f"  [DMManager] 새 DM (from {sender_id}): {text[:50]}...")
        reply_text = _generate_reply(text, persona)

        if not dry_run:
            success = _send_dm(sender_id, reply_text)
            if success:
                print(f"    → 답장 완료: {reply_text[:60]}...")
                replied_ids.add(conv_id)
            else:
                print(f"    → 답장 실패")
        else:
            print(f"    → [DRY RUN] {reply_text[:60]}...")
            replied_ids.add(conv_id)

        results.append(DMReply(
            conversation_id=conv_id,
            sender_id=sender_id,
            original_message=text,
            reply=reply_text,
        ))

    _save_replied(replied_ids)
    print(f"  [DMManager] 처리 완료: {len(results)}개 답장")
    return results


if __name__ == "__main__":
    replies = run(dry_run=True)
    for r in replies:
        print(f"\n[{r.sender_id}]: {r.original_message}")
        print(f"→ {r.reply}")
