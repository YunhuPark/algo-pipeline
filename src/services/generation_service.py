"""One evidence-grounded execution path for every card-news entrypoint."""
from __future__ import annotations

import json
import hashlib
import shutil
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from src.schemas.card_news import SourceLineage, TrendReport
from src.schemas.content_package import PipelineResult
from src.schemas.queue_schemas import (
    CollectionMethod,
    QueueEvidenceItem,
    QueueMetadataV2,
)


def build_verified_lineage(
    topic: str,
    trend_report: TrendReport,
    *,
    collection_method: CollectionMethod = CollectionMethod.NEWS_COLLECTOR,
    max_sources: int = 3,
) -> SourceLineage:
    """Convert collected articles into auditable multi-source lineage."""

    metadata = build_queue_metadata(topic, trend_report, max_sources=max_sources)
    return metadata.to_source_lineage(collection_method)


def build_queue_metadata(
    topic: str,
    trend_report: TrendReport,
    *,
    max_sources: int = 3,
) -> QueueMetadataV2:
    """Build Queue V2 metadata without collapsing the collected sources."""

    usable = [
        item
        for item in trend_report.results
        if item.title.strip()
        and item.url.startswith(("http://", "https://"))
        and item.content.strip()
    ]
    if not usable:
        raise ValueError("VERIFIED_SOURCE_MISSING")

    primary = usable[0]
    evidence = [
        QueueEvidenceItem(
            title=item.title,
            url=item.url,
            source="trend_analyzer",
            content=item.content,
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            source_material_level=(
                "full_article" if len(item.content) >= 1000 else "partial_article"
            ),
        )
        for item in usable[:max_sources]
    ]
    return QueueMetadataV2(
        topic=topic,
        source_title=primary.title,
        source_url=primary.url,
        context=primary.content,
        evidence=evidence,
    )


def collect_verified_lineage(
    topic: str,
    *,
    max_results: int = 5,
    selected_title: str = "",
    selected_url: str = "",
    selected_content: str = "",
) -> SourceLineage:
    """Collect full article text before any content generation starts."""

    from src.agents import trend_analyzer

    if selected_title or selected_url:
        report = trend_analyzer.build_locked_source_report(
            topic,
            title=selected_title,
            url=selected_url,
            content=selected_content,
        )
    else:
        report = trend_analyzer.run(topic, max_results=max_results)
    return build_verified_lineage(topic, report)


def _record_result_metadata(
    result: PipelineResult,
    run_id: str | None,
    source_lineage: SourceLineage,
) -> None:
    if not run_id or not result.image_paths:
        return
    output_dir = Path(result.image_paths[0]).parent
    meta_path = output_dir / "meta.json"
    payload: dict = {}
    if meta_path.exists():
        try:
            payload = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
    payload["run_id"] = run_id
    payload["generation_succeeded"] = result.generation_succeeded
    payload["publish_requested"] = result.publish_requested
    payload["publish_succeeded"] = result.publish_succeeded
    payload["approval_decision"] = result.approval_decision

    lineage_json = source_lineage.model_dump_json(indent=2)
    (output_dir / "source_lineage.json").write_text(lineage_json, encoding="utf-8")
    payload["source_lineage_sha256"] = hashlib.sha256(
        lineage_json.encode("utf-8")
    ).hexdigest()

    script_path = output_dir / "script.json"
    original_script_path = output_dir / "original_script.json"
    if script_path.exists():
        script_bytes = script_path.read_bytes()
        payload["script_sha256"] = hashlib.sha256(script_bytes).hexdigest()
        if not original_script_path.exists():
            shutil.copy2(script_path, original_script_path)
    payload.setdefault("content_revision", 0)
    payload.setdefault("editorial_validation_status", "ORIGINAL_VERIFIED")
    meta_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _start_tracking(topic: str, strategy_id: str) -> str | None:
    try:
        from src.analytics.db_experiments import init_tracking_db
        from src.db_tracking import start_run

        init_tracking_db()
        return start_run(topic, origin="real_pipeline", strategy_id=strategy_id)
    except Exception as exc:
        print(f"  [Tracking] 실행 기록 시작 실패: {type(exc).__name__}")
        return None


def _record_lineage(run_id: str | None, source_lineage: SourceLineage) -> None:
    """Persist independently attributable source URLs for audit and analysis."""
    if not run_id:
        return
    try:
        from src.db_tracking import log_source

        seen_urls: set[str] = set()
        for passage in source_lineage.evidence_passages:
            if passage.source_url in seen_urls:
                continue
            seen_urls.add(passage.source_url)
            title = (
                source_lineage.source_title
                if passage.source_url == source_lineage.source_url
                else passage.location or passage.source_url
            )
            log_source(run_id, passage.source_url, title)
    except Exception as exc:
        print(f"  [Tracking] 출처 계보 기록 실패: {type(exc).__name__}")


def _finish_tracking(
    run_id: str | None,
    result: PipelineResult,
    elapsed: float,
    strategy_id: str,
) -> None:
    if not run_id:
        return
    try:
        from src.db_tracking import end_run, link_run_publication, log_quality_check

        status = "SUCCESS" if result.generation_succeeded else "FAILED"
        end_run(
            run_id,
            status,
            latency=elapsed,
            error=result.error_code or "",
            strategy_id=strategy_id,
            grounded_claim_rate=1.0 if result.generation_succeeded else 0.0,
            retry_count=result.retry_count,
        )
        log_quality_check(
            run_id,
            "generation_quality_gate",
            result.generation_succeeded,
            result.error_code or "",
        )
        if result.publish_succeeded and result.ig_post_id:
            link_run_publication(run_id, result.ig_post_id)
    except Exception as exc:
        print(f"  [Tracking] 실행 기록 종료 실패: {type(exc).__name__}")


def _record_editorial_review(result: PipelineResult, run_id: str | None) -> None:
    """Persist an explicit terminal/Telegram review; never publish from here."""
    if (
        not run_id
        or result.approval_decision not in {"APPROVED", "REJECTED"}
        or not result.image_paths
    ):
        return
    try:
        from src.analytics.feedback import log_editorial_feedback

        content_id = result.image_paths[0].parent.name
        log_editorial_feedback(
            content_id=content_id,
            run_id=run_id,
            editor_id="supervised_operator",
            approval_decision=result.approval_decision,
            edit_reason_category=(
                "review_rejection" if result.approval_decision == "REJECTED" else ""
            ),
            review_duration_sec=result.review_duration_sec,
            idempotency_key=f"supervised-review:{run_id}:{result.approval_decision}",
        )
    except Exception as exc:
        print(f"  [Tracking] 사람 검토 기록 실패: {type(exc).__name__}")


def execute_generation(
    *,
    topic: str,
    source_lineage: SourceLineage,
    publish: bool = False,
    make_reels: bool = False,
    template: str = "brand",
    angle_hint: str = "",
    num_cards: int | None = None,
    handle: str = "",
    force_dalle: bool = False,
    force_refresh: bool = False,
    select_angle: bool = False,
    human_approval: bool = False,
    auto: bool = True,
    publish_attempt_id: str | None = None,
    before_publish: Callable[[str], None] | None = None,
    on_remote_id: Callable[[str, str], None] | None = None,
) -> PipelineResult:
    """Run the canonical pipeline and always return a typed result.

    This function does not weaken publish guards.  Callers still need the
    durable Queue V2 publish attempt when ``publish=True``.
    """

    if not source_lineage.is_verified_ready:
        raise ValueError("VERIFIED_SOURCE_LINEAGE_REQUIRED")
    if human_approval and auto:
        raise ValueError("HUMAN_APPROVAL_REQUIRES_INTERACTIVE_MODE")
    if publish and (
        not publish_attempt_id or before_publish is None or on_remote_id is None
    ):
        raise ValueError("DURABLE_QUEUE_PUBLISH_ATTEMPT_REQUIRED")

    from src import pipeline
    from src.persona import load_persona
    from src.schemas.queue_schemas import PublishAttemptState

    card_strategy = str(num_cards) if num_cards is not None else "persona-default"
    angle_strategy = (
        "hint" if angle_hint else "selector" if select_angle else "default"
    )
    strategy_id = (
        f"template:{template}|cards:{card_strategy}|angle:{angle_strategy}"
    )
    run_id = _start_tracking(topic, strategy_id)
    _record_lineage(run_id, source_lineage)
    started = time.monotonic()
    context = source_lineage.context
    if angle_hint:
        context = f"{context}\n[앵글 힌트] {angle_hint}".strip()

    try:
        result = pipeline.run_pipeline(
            topic=topic,
            persona=load_persona(),
            num_cards=num_cards,
            handle=handle,
            force_dalle=force_dalle,
            force_refresh=force_refresh,
            trend_context=context,
            publish=publish,
            auto=auto,
            make_reels=make_reels,
            template=template,
            select_angle=select_angle,
            human_approval=human_approval,
            topic_refined=True,
            source_lineage=source_lineage,
            publish_attempt_id=publish_attempt_id,
            before_publish=before_publish,
            on_remote_id=on_remote_id,
        )
        if result is None:
            result = PipelineResult(
                image_paths=[],
                generation_succeeded=False,
                publish_requested=publish,
                publish_succeeded=False,
                ig_post_id=None,
                permalink=None,
                failure_stage="pipeline",
                error_code="EMPTY_PIPELINE_RESULT",
                publish_attempt_state=PublishAttemptState.NOT_ATTEMPTED,
                publish_attempt_id=publish_attempt_id,
            )
    except Exception:
        if run_id:
            failed = PipelineResult(
                image_paths=[],
                generation_succeeded=False,
                publish_requested=publish,
                publish_succeeded=False,
                ig_post_id=None,
                permalink=None,
                failure_stage="pipeline",
                error_code="PIPELINE_EXCEPTION",
                publish_attempt_state=PublishAttemptState.NOT_ATTEMPTED,
                publish_attempt_id=publish_attempt_id,
                run_id=run_id,
            )
            _finish_tracking(run_id, failed, time.monotonic() - started, strategy_id)
        raise

    result = replace(result, run_id=run_id)
    elapsed = time.monotonic() - started
    _finish_tracking(run_id, result, elapsed, strategy_id)
    try:
        _record_result_metadata(result, run_id, source_lineage)
    except OSError as exc:
        print(f"  [Tracking] meta.json 기록 실패: {type(exc).__name__}")
    _record_editorial_review(result, run_id)
    return result
