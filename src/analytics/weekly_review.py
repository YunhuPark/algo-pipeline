"""Deterministic weekly card-news quality review.

The review observes production pipeline data and produces one human-reviewable
experiment proposal.  It never publishes media, changes policy, or activates an
experiment.
"""
from __future__ import annotations

import json
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from src.analytics.db_experiments import init_tracking_db, tracking_db_path
from src.db_factory import get_connection


TRACKING_DB_PATH: Path | None = None
MIN_REAL_RUNS = 3
MIN_EDITORIAL_FEEDBACK = 1


def _path() -> Path:
    return Path(TRACKING_DB_PATH) if TRACKING_DB_PATH is not None else tracking_db_path()


def default_review_window(now: datetime | None = None) -> tuple[datetime, datetime]:
    """Return the most recently completed Monday-to-Monday UTC window."""

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    this_monday = (current - timedelta(days=current.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return this_monday - timedelta(days=7), this_monday


def _rows(conn, sql: str, *params: str):
    return conn.execute(sql, params).fetchall()


def _proposal(metrics: dict[str, Any], top_issues: list[dict[str, Any]]) -> dict[str, Any]:
    leading = top_issues[0]["code"] if top_issues else "EDITORIAL_EFFORT"
    if metrics["quality_failure_count"]:
        change = f"가장 잦은 품질 실패 '{leading}'를 생성 직후 검증 규칙으로 조기 차단"
        metric = "quality_gate_pass_rate"
        hypothesis = "실패 원인을 렌더링 전에 차단하면 재시도와 검토 시간이 줄어든다."
    elif metrics["avg_text_edit_ratio"] >= 0.2:
        change = "초안 생성 전에 근거별 핵심 주장 표를 먼저 만들도록 프롬프트 순서 변경"
        metric = "avg_text_edit_ratio"
        hypothesis = "근거-주장 구조를 먼저 고정하면 편집자의 문장 수정량이 줄어든다."
    elif metrics["mature_snapshot_count"] and metrics["save_rate"] < 0.02:
        change = "저장 가치가 드러나는 요약형 CTA를 기존 CTA와 비교"
        metric = "save_rate"
        hypothesis = "실용적 요약 CTA가 게시물 저장률을 높인다."
    else:
        change = "현재 브랜드 템플릿과 정보 밀도 10% 감소안을 비교"
        metric = "avg_review_duration_sec"
        hypothesis = "정보 밀도를 낮추면 사실성을 유지하면서 검토 시간이 줄어든다."

    return {
        "status": "DRAFT",
        "title": "주간 품질 개선 실험 1건",
        "hypothesis": hypothesis,
        "change": change,
        "primary_metric": metric,
        "automatic_activation": False,
        "requires_human_approval": True,
    }


def run_weekly_quality_review(
    *,
    week_start: datetime | None = None,
    week_end: datetime | None = None,
) -> dict[str, Any]:
    """Aggregate one completed week and persist an idempotent report."""

    default_start, default_end = default_review_window()
    start = week_start or default_start
    end = week_end or default_end
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    if start >= end:
        raise ValueError("week_start must be earlier than week_end")

    target = _path()
    init_tracking_db(target)
    start_text = start.astimezone(timezone.utc).isoformat()
    end_text = end.astimezone(timezone.utc).isoformat()
    idempotency_key = f"weekly-quality:{start.date()}:{end.date()}"

    with get_connection(target) as conn:
        existing = conn.execute(
            "SELECT report_json FROM weekly_quality_reviews WHERE idempotency_key=?",
            (idempotency_key,),
        ).fetchone()
        if existing:
            report = json.loads(existing["report_json"])
            report["idempotent"] = True
            return report

        runs = _rows(
            conn,
            """SELECT run_id, status, cost_usd, latency_sec, error_msg,
                      grounded_claim_rate, retry_count
               FROM content_runs
               WHERE origin='real_pipeline'
                 AND datetime(created_at) >= datetime(?)
                 AND datetime(created_at) < datetime(?)""",
            start_text,
            end_text,
        )
        feedback = _rows(
            conn,
            """SELECT f.feedback_id, f.content_id, f.approval_decision,
                      f.edit_reason_category, f.created_at,
                      f.text_edit_ratio, f.claim_correction_count,
                      f.editorial_effort_score, f.review_duration_sec
               FROM editorial_feedback_events f
               JOIN content_runs r ON r.run_id=f.run_id
               WHERE r.origin='real_pipeline'
                 AND datetime(f.created_at) >= datetime(?)
                 AND datetime(f.created_at) < datetime(?)
               ORDER BY datetime(f.created_at), f.feedback_id""",
            start_text,
            end_text,
        )
        quality_failures = _rows(
            conn,
            """SELECT q.check_type, q.reason
               FROM quality_checks q
               JOIN content_runs r ON r.run_id=q.run_id
               WHERE r.origin='real_pipeline' AND q.passed=0
                 AND datetime(r.created_at) >= datetime(?)
                 AND datetime(r.created_at) < datetime(?)""",
            start_text,
            end_text,
        )
        snapshots = _rows(
            conn,
            """WITH ranked AS (
                   SELECT p.publication_id, p.reach, p.saves, p.shares,
                          p.likes, p.comments,
                          ROW_NUMBER() OVER (
                              PARTITION BY p.publication_id
                              ORDER BY datetime(p.measured_at) DESC,
                                       p.snapshot_id DESC
                          ) AS snapshot_rank
                   FROM performance_snapshots p
                   JOIN run_publications rp
                     ON rp.publication_id=p.publication_id
                   JOIN content_runs r ON r.run_id=rp.run_id
                   WHERE p.is_provisional=0
                     AND r.origin='real_pipeline'
                     AND datetime(r.created_at) >= datetime(?)
                     AND datetime(r.created_at) < datetime(?)
                     AND datetime(p.measured_at) >= datetime(?)
                     AND datetime(p.measured_at) < datetime(?)
               )
               SELECT reach, saves, shares, likes, comments
               FROM ranked
               WHERE snapshot_rank=1""",
            start_text,
            end_text,
            start_text,
            end_text,
        )

        run_count = len(runs)
        success_count = sum(str(row["status"]).upper() == "SUCCESS" for row in runs)
        feedback_count = len(feedback)
        decision_by_content = {
            str(row["content_id"]): row
            for row in feedback
            if str(row["approval_decision"]).upper() in {"APPROVED", "REJECTED"}
        }
        decision_events = list(decision_by_content.values())
        edit_events = [
            row for row in feedback
            if str(row["approval_decision"]).upper() == "EDITED"
            or float(row["text_edit_ratio"] or 0) > 0
        ]
        review_durations: dict[str, float] = {}
        for row in feedback:
            content_id = str(row["content_id"])
            review_durations[content_id] = max(
                review_durations.get(content_id, 0.0),
                float(row["review_duration_sec"] or 0),
            )
        total_reach = sum(int(row["reach"] or 0) for row in snapshots)
        metrics = {
            "real_run_count": run_count,
            "success_count": success_count,
            "generation_success_rate": round(success_count / run_count, 4) if run_count else 0.0,
            "quality_failure_count": len(quality_failures),
            "avg_retry_count": round(sum(int(row["retry_count"] or 0) for row in runs) / run_count, 3) if run_count else 0.0,
            "avg_latency_sec": round(sum(float(row["latency_sec"] or 0) for row in runs) / run_count, 3) if run_count else 0.0,
            "total_cost_usd": round(sum(float(row["cost_usd"] or 0) for row in runs), 4),
            "avg_grounded_claim_rate": round(sum(float(row["grounded_claim_rate"] or 0) for row in runs) / run_count, 4) if run_count else 0.0,
            "editorial_feedback_count": feedback_count,
            "review_decision_count": len(decision_events),
            "reviewed_content_count": len(review_durations),
            "approval_rate": round(sum(str(row["approval_decision"]).upper() == "APPROVED" for row in decision_events) / len(decision_events), 4) if decision_events else 0.0,
            "avg_text_edit_ratio": round(sum(float(row["text_edit_ratio"] or 0) for row in edit_events) / len(edit_events), 4) if edit_events else 0.0,
            "claim_correction_count": sum(int(row["claim_correction_count"] or 0) for row in feedback),
            "avg_editorial_effort_score": round(sum(float(row["editorial_effort_score"] or 0) for row in feedback) / feedback_count, 3) if feedback_count else 0.0,
            "avg_review_duration_sec": round(sum(review_durations.values()) / len(review_durations), 2) if review_durations else 0.0,
            "mature_snapshot_count": len(snapshots),
            "save_rate": round(sum(int(row["saves"] or 0) for row in snapshots) / total_reach, 4) if total_reach else 0.0,
            "share_rate": round(sum(int(row["shares"] or 0) for row in snapshots) / total_reach, 4) if total_reach else 0.0,
        }

        issue_counts: Counter[str] = Counter()
        for row in quality_failures:
            issue_counts[str(row["check_type"] or row["reason"] or "QUALITY_FAILURE")] += 1
        for row in runs:
            if row["error_msg"]:
                issue_counts[str(row["error_msg"])] += 1
        for row in feedback:
            if row["edit_reason_category"]:
                issue_counts[f"EDIT:{row['edit_reason_category']}"] += 1
        top_issues = [
            {"code": code, "count": count}
            for code, count in issue_counts.most_common(3)
        ]

        enough_data = run_count >= MIN_REAL_RUNS and len(decision_events) >= MIN_EDITORIAL_FEEDBACK
        status = "READY" if enough_data else "INSUFFICIENT_DATA"
        report = {
            "review_id": str(uuid.uuid4()),
            "week_start": start_text,
            "week_end": end_text,
            "status": status,
            "data_requirements": {
                "min_real_runs": MIN_REAL_RUNS,
                "min_review_decisions": MIN_EDITORIAL_FEEDBACK,
            },
            "metrics": metrics,
            "top_issues": top_issues,
            "experiment_proposal": _proposal(metrics, top_issues) if enough_data else None,
            "safety": {
                "published": False,
                "policy_changed": False,
                "experiment_activated": False,
            },
            "idempotent": False,
        }
        conn.execute(
            """INSERT INTO weekly_quality_reviews (
                   review_id, week_start, week_end, status, report_json,
                   idempotency_key
               ) VALUES (?, ?, ?, ?, ?, ?)""",
            (
                report["review_id"],
                start_text,
                end_text,
                status,
                json.dumps(report, ensure_ascii=False, sort_keys=True),
                idempotency_key,
            ),
        )
    return report


def list_weekly_quality_reviews(limit: int = 12) -> list[dict[str, Any]]:
    target = _path()
    init_tracking_db(target)
    with get_connection(target) as conn:
        rows = conn.execute(
            "SELECT report_json FROM weekly_quality_reviews ORDER BY week_end DESC LIMIT ?",
            (max(1, min(limit, 52)),),
        ).fetchall()
    return [json.loads(row["report_json"]) for row in rows]


def get_latest_weekly_quality_review() -> dict[str, Any] | None:
    items = list_weekly_quality_reviews(limit=1)
    return items[0] if items else None
