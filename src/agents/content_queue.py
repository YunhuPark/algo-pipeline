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
from datetime import timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from src.db import (
    enqueue_v2,
    dequeue_next,
    insert_post,
    mark_queue_error,
    set_queue_image_dir,
    start_publish_attempt,
    store_queue_ig_post_id,
    complete_queue_publish,
    mark_queue_status,
    queue_count,
    get_queue,
)
from src.schemas.queue_schemas import CollectionMethod, PublishAttemptState, QueueMetadataV2


RETRYABLE_PRE_PUBLISH_ERRORS = {
    "NETWORK_TIMEOUT_BEFORE_PUBLISH",
    "RATE_LIMITED_BEFORE_PUBLISH",
    "TEMPORARY_UPSTREAM_UNAVAILABLE",
    "PIPELINE_PRE_PUBLISH_TRANSIENT",
}


def _validate_publish_configuration() -> None:
    """Fail before dequeue/attempt mutation when remote publishing is unsafe."""
    from src.agents.publisher import validate_publish_config, verify_instagram_account

    validate_publish_config()
    verify_instagram_account()


def _collect_news():
    from src.agents.news_collector import collect_and_select

    return collect_and_select()


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
        raise ValueError("직접 주제는 출처 attestation이 없어 Queue V2에 등록할 수 없습니다.")

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
            news = _collect_news()

            # A selected headline without a real source cannot become
            # publication evidence. Keep the queue fail-closed before the
            # follow-up article fetch.
            selected_item = getattr(news, "selected_item", None)
            if selected_item is None:
                print("  [ContentQueue] 검증 가능한 뉴스 출처 없음 — 큐 추가 생략")
                continue

            # 중복 주제 회피
            topic = news.topic
            if topic in seen_topics:
                topic = f"{topic} (심화)"
            seen_topics.add(topic)

            # Keep the editor-selected article URL as the primary evidence.
            # Re-searching a generated topic here can silently switch events.
            from src.agents import trend_analyzer
            from src.services.generation_service import build_queue_metadata

            report = trend_analyzer.build_locked_source_report(
                topic,
                title=selected_item.title,
                url=selected_item.url,
                content=selected_item.summary,
            )
            metadata = build_queue_metadata(topic, report)
            row_id = enqueue_v2(
                metadata,
                CollectionMethod.NEWS_COLLECTOR,
            )
            ids.append(row_id)
            print(f"  [ContentQueue] 뉴스 큐 추가: {topic}")
        except Exception as e:
            print(f"  [ContentQueue] 뉴스 수집 실패 ({i+1}/{count}): {e}")

    return ids


def publish_next(
    publish_to_ig: bool = True,
    *,
    require_human_approval: bool = True,
) -> dict[str, Any] | None:
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
    if publish_to_ig:
        _validate_publish_configuration()

    # 맨 앞 항목의 메타데이터가 영구적으로 무효(HASH_MISMATCH 등)면 그 항목만
    # 차단하고 다음 항목으로 넘어간다 — 안 그러면 오래된 손상 데이터 하나가
    # 뒤에 있는 멀쩡한 항목까지 전부 막아버린다. mark_queue_error가 매번
    # publish_error_code를 기록해 같은 항목을 다시 dequeue하지 않으므로,
    # 큐 길이를 넘는 반복은 나지 않는다 — 그래도 상한을 둬 방어한다.
    row = None
    metadata = None
    for _ in range(50):
        candidate = dequeue_next()
        if candidate is None:
            print("  [ContentQueue] 대기 중인 큐가 없습니다.")
            return None
        candidate_metadata, error = _load_queue_metadata(candidate)
        if error:
            mark_queue_error(
                candidate["id"], error, increment_retry=False, preserve_attempt=True
            )
            print(
                f"  [ContentQueue] 게시 차단: {error} (큐 id={candidate['id']}) "
                "→ 다음 항목 시도"
            )
            continue
        row = candidate
        metadata = candidate_metadata
        break
    else:
        print("  [ContentQueue] 유효한 큐 항목을 찾지 못했습니다.")
        return None

    queue_id = row["id"]
    topic = row["topic"]
    context = row["context"] or ""
    angle_hint = row["angle_hint"] or ""
    image_dir = row["image_dir"] or ""
    assert metadata is not None
    collection_method = CollectionMethod(row["collection_method"])
    source_lineage = metadata.to_source_lineage(collection_method)

    attempt_id = str(uuid4()) if publish_to_ig else None

    def before_publish(value: str) -> None:
        start_publish_attempt(
            queue_id,
            value,
            datetime.now(timezone.utc).isoformat(),
        )

    def on_remote_id(value: str, post_id: str) -> None:
        store_queue_ig_post_id(queue_id, value, post_id)

    def run_full_pipeline():
        args = (
            topic,
            context,
            angle_hint,
            publish_to_ig,
            attempt_id,
            before_publish,
            on_remote_id,
            source_lineage,
        )
        if require_human_approval:
            return _run_full_pipeline(*args)
        return _run_full_pipeline(*args, require_human_approval=False)

    print(f"\n  [ContentQueue] 발행 시작: '{topic}' (큐 id={queue_id})")

    try:
        # ── 이미 렌더링된 경우 ─────────────────────────────
        from src.schemas.content_package import PipelineResult, PublishError
        res = None
        if image_dir and Path(image_dir).exists():
            print(f"  [ContentQueue] 기존 렌더링 사용: {image_dir}")
            paths = sorted(Path(image_dir).glob("card_*.png"))
            if not paths:
                print("  [ContentQueue] PNG 없음 — 전체 파이프라인 실행")
                res = run_full_pipeline()
            elif publish_to_ig:
                # 사람이 이미 검토·승인한 바로 그 렌더링을 그대로 올린다 —
                # 여기서 다시 생성하면 승인한 화면과 실제 게시물이 달라질 수 있다.
                return _publish_cached_render(
                    queue_id, image_dir, topic, attempt_id, before_publish, on_remote_id
                )
            else:
                print("  [ContentQueue] 이미 준비됨 — 재생성 없이 그대로 반환")
                return {"id": queue_id, "topic": topic, "paths": paths}
        else:
            res = run_full_pipeline()

        if res and hasattr(res, 'image_paths') and res.image_paths:
            if res.approval_decision == "REJECTED":
                mark_queue_status(queue_id, "skipped")
                print(f"  [ContentQueue] 사람 검토에서 게시 반려 (큐 id={queue_id})")
                return None
            if res.publish_requested:
                if res.publish_succeeded and res.ig_post_id:
                    complete_queue_publish(queue_id, attempt_id, res.ig_post_id)
                    print(f"  [ContentQueue] 발행 완료: {topic} ({len(res.image_paths)}장)")
                    return {"id": queue_id, "topic": topic, "paths": res.image_paths}
                else:
                    print(f"  [ContentQueue] 게시 실패 (큐 id={queue_id}): {res.error_code}")
                    retryable = (
                        res.publish_attempt_state == PublishAttemptState.NOT_ATTEMPTED
                        and res.error_code in RETRYABLE_PRE_PUBLISH_ERRORS
                    )
                    mark_queue_error(
                        queue_id,
                        res.error_code or "UNKNOWN_PIPELINE_ERROR",
                        increment_retry=retryable,
                        preserve_attempt=not retryable,
                    )
                    return None
            else:
                # generation only
                if res.image_paths:
                    set_queue_image_dir(queue_id, str(res.image_paths[0].parent))
                mark_queue_status(queue_id, "ready")
                return {"id": queue_id, "topic": topic, "paths": res.image_paths}
        elif type(res) == list and len(res) > 0: # fallback for paths directly
            # shouldn't happen but just in case
            mark_queue_error(queue_id, "UNKNOWN_PIPELINE_ERROR")
            return None
        else:
            print(f"  [ContentQueue] 발행 실패 (빈 경로): {topic}")
            mark_queue_error(queue_id, "EMPTY_PIPELINE_RESULT")
            return None

    except Exception as e:
        print(f"  [ContentQueue] 파이프라인 오류 (큐 id={queue_id}): {e}")
        mark_queue_error(queue_id, "UNKNOWN_PIPELINE_EXCEPTION")
        raise


def _publish_cached_render(
    queue_id: int,
    image_dir: str,
    topic: str,
    attempt_id: str | None,
    before_publish,
    on_remote_id,
) -> dict[str, Any] | None:
    """Upload an already-rendered 'ready' row's cards without regenerating.

    Mirrors the durable-attempt bookkeeping run_full_pipeline()'s own publish
    step performs (before_publish -> IG upload -> on_remote_id ->
    complete_queue_publish), but skips generation entirely so the exact cards
    a human already reviewed are what gets posted.
    """
    import json as _json
    from datetime import datetime, timezone
    from src.agents import publisher as ig_publisher

    folder = Path(image_dir)
    paths = sorted(folder.glob("card_*.png"))
    if not paths:
        # 승인된 렌더링 자체가 사라진 것 — 이 항목이 잘못된 게 아니라 재생성이
        # 필요한 상황이므로, 영구 오류로 막는 대신 'pending'으로 되돌려 다음
        # 생성 시도에서 다시 만들 수 있게 한다.
        print(f"  [ContentQueue] 캐시된 렌더링을 찾을 수 없음 (큐 id={queue_id}) → 재생성 가능하도록 되돌립니다.")
        set_queue_image_dir(queue_id, "")
        mark_queue_status(queue_id, "pending")
        return None

    script_path = folder / "script.json"
    script_data = None
    if script_path.exists():
        try:
            script_data = _json.loads(script_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            script_data = None
    if not isinstance(script_data, dict):
        print(f"  [ContentQueue] 캐시된 script.json 없음/손상 (큐 id={queue_id}) → 재생성 가능하도록 되돌립니다.")
        set_queue_image_dir(queue_id, "")
        mark_queue_status(queue_id, "pending")
        return None

    hook = script_data.get("hook") or ""
    raw_hashtags = script_data.get("hashtags") or []
    hashtags = [str(h) for h in raw_hashtags] if isinstance(raw_hashtags, list) else []

    if attempt_id and before_publish:
        try:
            before_publish(attempt_id)
        except Exception:
            mark_queue_error(queue_id, "LOCAL_ATTEMPT_PERSISTENCE_ERROR", preserve_attempt=True)
            return None

    try:
        ig_post_id = ig_publisher.publish(image_paths=paths, hook=hook, hashtags=hashtags)
    except Exception as e:
        print(f"  [ContentQueue] 캐시 렌더링 발행 실패 (큐 id={queue_id}): {e}")
        mark_queue_error(queue_id, "REMOTE_PUBLISH_PERSISTENCE_UNCERTAIN")
        return None

    if not ig_post_id:
        mark_queue_error(queue_id, "UNCERTAIN_EMPTY_POST_ID")
        return None

    if attempt_id and on_remote_id:
        try:
            on_remote_id(attempt_id, ig_post_id)
        except Exception:
            mark_queue_error(queue_id, "REMOTE_PUBLISH_PERSISTENCE_UNCERTAIN")
            return None

    insert_post(
        platform="instagram",
        topic=topic,
        post_id=ig_post_id,
        hook=hook,
        hashtags=hashtags,
        image_dir=str(folder),
        posted_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
    )
    complete_queue_publish(queue_id, attempt_id, ig_post_id)
    print(f"  [ContentQueue] 캐시 렌더링 발행 완료: {topic} ({len(paths)}장)")
    return {"id": queue_id, "topic": topic, "paths": paths}


def _run_full_pipeline(
    topic: str,
    context: str,
    angle_hint: str,
    publish: bool,
    publish_attempt_id: str | None = None,
    before_publish=None,
    on_remote_id=None,
    source_lineage=None,
    require_human_approval: bool = True,
):
    """Canonical pipeline helper shared with the dashboard."""
    from src.services.generation_service import execute_generation

    return execute_generation(
        topic=topic,
        source_lineage=source_lineage,
        publish=publish,
        angle_hint=angle_hint,
        publish_attempt_id=publish_attempt_id,
        before_publish=before_publish,
        on_remote_id=on_remote_id,
        human_approval=bool(publish and require_human_approval),
        auto=not require_human_approval,
    )


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
    raise ValueError("Queue V2는 검증된 출처 evidence가 필수이며 직접 주제 등록을 지원하지 않습니다.")


def _load_queue_metadata(row: Any) -> tuple[QueueMetadataV2 | None, str | None]:
    import json
    import hashlib

    method = row["collection_method"] if "collection_method" in row.keys() else None
    if method == CollectionMethod.LEGACY_UNVERIFIED.value:
        return None, "LEGACY_UNSUPPORTED"
    if method not in {CollectionMethod.NEWS_COLLECTOR.value, CollectionMethod.MANUAL_VERIFIED.value}:
        return None, "UNPUBLISHABLE_METHOD"
    if row["metadata_schema_version"] != 2:
        return None, "MISSING_SCHEMA_VERSION"
    try:
        raw = row["metadata_json"]
        data = json.loads(raw)
        metadata = QueueMetadataV2.model_validate(data)
    except Exception:
        return None, "JSON_PARSE_ERROR"
    canonical = metadata.canonical_json()
    actual = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    if actual != row["lineage_hash"]:
        return None, "HASH_MISMATCH"
    if metadata.topic != row["topic"] or metadata.context != (row["context"] or ""):
        return None, "METHOD_MISMATCH"
    try:
        metadata.to_source_lineage(CollectionMethod(method))
    except Exception:
        return None, "INVALID_SOURCE_LINEAGE"
    return metadata, None


def _validate_queue_row(row: Any) -> str | None:
    """Compatibility wrapper for callers that only need the error code."""
    return _load_queue_metadata(row)[1]


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
