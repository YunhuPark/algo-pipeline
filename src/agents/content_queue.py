"""
ContentQueue — 콘텐츠 큐 관리
────────────────────────────────────────────────────────
여러 카드뉴스를 미리 기획해두고 순서대로 발행합니다.
이미지 렌더링은 발행 시점에 수행합니다.

공개 API:
  bulk_generate(count, topics, auto_news)   — N개 미리 기획해서 큐에 저장
  publish_next(publish_to_ig)               — 큐 다음 항목을 전체 파이프라인으로 실행
  add_topic(topic, context, scheduled_at)   — 단일 주제 큐 추가
  get_status()                              — 큐 현황 dict 반환
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from src.db import (
    enqueue,
    dequeue_next,
    mark_queue_status,
    queue_count,
    get_queue,
)
from src.agents.news_collector import collect_and_select


# ── 공개 함수 ──────────────────────────────────────────────

def bulk_generate(
    count: int,
    topics: list[str] | None = None,
    auto_news: bool = True,
) -> list[int]:
    """
    N개의 콘텐츠 항목을 미리 기획해 큐에 저장합니다.
    이미지 렌더링은 발행 시점(publish_next)에 수행됩니다.

    Args:
        count:      큐에 추가할 항목 수
        topics:     직접 지정할 주제 목록. None이면 auto_news로 자동 수집.
        auto_news:  True → topics가 None일 때 news_collector로 자동 수집

    Returns:
        생성된 queue row id 목록
    """
    ids: list[int] = []

    if topics:
        # 직접 지정된 주제 사용
        for i in range(min(count, len(topics))):
            row_id = enqueue(
                topic=topics[i],
                context="",
                angle_hint="",
            )
            ids.append(row_id)
            print(f"  [ContentQueue] 큐 추가 ({i+1}/{count}): {topics[i]}")

        # 주제가 count보다 적으면 나머지는 뉴스 수집으로 채우기
        remaining = count - len(topics)
        if remaining > 0 and auto_news:
            print(f"  [ContentQueue] 부족한 {remaining}개를 뉴스에서 수집합니다...")
            ids.extend(_fill_from_news(remaining))

    elif auto_news:
        ids.extend(_fill_from_news(count))

    else:
        print("  [ContentQueue] topics와 auto_news 모두 없음 — 아무것도 추가하지 않습니다.")

    print(f"  [ContentQueue] bulk_generate 완료: {len(ids)}개 큐 추가")
    return ids


def _fill_from_news(count: int) -> list[int]:
    """뉴스 수집을 count번 반복해 큐에 저장."""
    ids: list[int] = []
    seen_topics: set[str] = set()

    for i in range(count):
        try:
            print(f"  [ContentQueue] 뉴스 수집 중 ({i+1}/{count})...")
            news = collect_and_select()

            # 중복 주제 회피
            topic = news.topic
            if topic in seen_topics:
                topic = f"{topic} (심화)"
            seen_topics.add(topic)

            row_id = enqueue(
                topic=topic,
                context=news.context,
                angle_hint="",
            )
            ids.append(row_id)
            print(f"  [ContentQueue] 뉴스 큐 추가: {topic}")
        except Exception as e:
            print(f"  [ContentQueue] 뉴스 수집 실패 ({i+1}/{count}): {e}")

    return ids


def publish_next(publish_to_ig: bool = True) -> dict[str, Any] | None:
    """
    큐에서 다음 항목을 꺼내 전체 파이프라인을 실행합니다.

    - image_dir가 이미 있으면 렌더링 스킵, 바로 업로드
    - 없으면 full pipeline 실행
    - 성공 시 mark_queue_status(id, 'published')

    Args:
        publish_to_ig: True → Instagram 업로드까지 실행

    Returns:
        {"id": queue_id, "topic": topic, "paths": [Path, ...]} or None
    """
    from src import pipeline
    from src.persona import load_persona

    row = dequeue_next()
    if row is None:
        print("  [ContentQueue] 대기 중인 큐가 없습니다.")
        return None

    queue_id = row["id"]
    topic = row["topic"]
    context = row["context"] or ""
    angle_hint = row["angle_hint"] or ""
    image_dir = row["image_dir"] or ""

    print(f"\n  [ContentQueue] 발행 시작: '{topic}' (큐 id={queue_id})")

    try:
        # ── 이미 렌더링된 경우 ─────────────────────────────
        if image_dir and Path(image_dir).exists():
            print(f"  [ContentQueue] 기존 렌더링 사용: {image_dir}")
            paths = sorted(Path(image_dir).glob("*.png"))
            if not paths:
                print("  [ContentQueue] PNG 없음 — 전체 파이프라인 실행")
                paths = _run_full_pipeline(
                    topic, context, angle_hint, publish_to_ig
                )
        else:
            paths = _run_full_pipeline(
                topic, context, angle_hint, publish_to_ig
            )

        if paths:
            mark_queue_status(queue_id, "published")
            print(f"  [ContentQueue] 발행 완료: {topic} ({len(paths)}장)")
            return {"id": queue_id, "topic": topic, "paths": paths}
        else:
            print(f"  [ContentQueue] 발행 실패 (빈 경로): {topic}")
            mark_queue_status(queue_id, "skipped")
            return None

    except Exception as e:
        print(f"  [ContentQueue] 파이프라인 오류 (큐 id={queue_id}): {e}")
        mark_queue_status(queue_id, "skipped")
        raise


def _run_full_pipeline(
    topic: str,
    context: str,
    angle_hint: str,
    publish: bool,
) -> list[Path]:
    """파이프라인 실행 헬퍼."""
    from src import pipeline
    from src.persona import load_persona

    persona = load_persona()
    trend_context = context
    if angle_hint:
        trend_context = f"{context}\n[앵글 힌트] {angle_hint}".strip()

    paths = pipeline.run_pipeline(
        topic=topic,
        persona=persona,
        trend_context=trend_context,
        publish=publish,
        auto=True,
    )
    return paths


def add_topic(
    topic: str,
    context: str = "",
    scheduled_at: str | None = None,
) -> int:
    """
    단일 주제를 큐에 추가합니다.

    Args:
        topic:        카드뉴스 주제
        context:      배경 정보 (선택)
        scheduled_at: 예약 발행 시각 "YYYY-MM-DD HH:MM:SS" (None이면 즉시 대기열)

    Returns:
        생성된 queue row id
    """
    row_id = enqueue(
        topic=topic,
        context=context,
        angle_hint="",
        scheduled_at=scheduled_at,
    )
    sched_label = f" (예약: {scheduled_at})" if scheduled_at else ""
    print(f"  [ContentQueue] 추가: '{topic}'{sched_label} → id={row_id}")
    return row_id


def get_status() -> dict[str, Any]:
    """
    큐 현황을 dict로 반환합니다.

    Returns:
        {
            "pending":    N,
            "ready":      N,
            "published":  N,
            "skipped":    N,
            "next_topic": "다음 주제" or None,
        }
    """
    pending_count = queue_count("pending")
    ready_count = queue_count("ready")
    published_count = queue_count("published")
    skipped_count = queue_count("skipped")

    next_row = dequeue_next()
    next_topic = next_row["topic"] if next_row else None

    return {
        "pending": pending_count,
        "ready": ready_count,
        "published": published_count,
        "skipped": skipped_count,
        "next_topic": next_topic,
    }
