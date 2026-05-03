"""
알고 AI Agent 스케줄러
────────────────────────────────────────────────────────
실행: python src/scheduler.py

스케줄:
  매일 09:00  — 큐에서 꺼내거나 뉴스 수집 → 카드뉴스 → 업로드
  30분마다    — 새 댓글 AI 자동 답글
  1시간마다   — 새 DM AI 자동 답장
  매주 월요일 — 성과 분석 + 경쟁 계정 분석

환경 변수 (전부 선택):
  AGENT_POST_HOUR   = 9      # 업로드 시간 (기본 9)
  AGENT_COMMENT_MIN = 30     # 댓글 체크 주기(분)
  AGENT_DM_MIN      = 60     # DM 체크 주기(분)
  AGENT_AUTO_UPLOAD = true   # Instagram 자동 업로드
  AGENT_THREADS     = false  # Threads 동시 발행
  AGENT_BLOG        = false  # 블로그 동시 발행
  AGENT_DRY_RUN     = false  # 시뮬레이션 모드
  AGENT_TEMPLATE    = auto   # 디자인 템플릿
"""
from __future__ import annotations

import io
import os
import sys
import traceback
from datetime import datetime

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from dotenv import load_dotenv

load_dotenv()

POST_HOUR       = int(os.getenv("AGENT_POST_HOUR",    "9"))
COMMENT_MIN     = int(os.getenv("AGENT_COMMENT_MIN",  "30"))
DM_MIN          = int(os.getenv("AGENT_DM_MIN",       "60"))
AUTO_UPLOAD     = os.getenv("AGENT_AUTO_UPLOAD", "true").lower()  == "true"
AGENT_THREADS   = os.getenv("AGENT_THREADS",     "false").lower() == "true"
AGENT_BLOG      = os.getenv("AGENT_BLOG",        "false").lower() == "true"
DRY_RUN         = os.getenv("AGENT_DRY_RUN",     "false").lower() == "true"
AGENT_TEMPLATE  = os.getenv("AGENT_TEMPLATE",    "auto")


def _log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


# ── 작업 정의 ─────────────────────────────────────────────

def job_daily_cardnews() -> None:
    """매일: 큐 → (없으면 뉴스 수집) → 카드뉴스 → 업로드."""
    _log("=" * 55)
    _log("[알고] 일일 카드뉴스 작업 시작")
    try:
        from src.agents.content_queue import publish_next, get_status
        from src import pipeline
        from src.agents.news_collector import collect_and_select

        status = get_status()
        _log(f"  큐 대기: {status['pending']}개")

        if status["pending"] > 0:
            # 큐에서 발행
            _log("  큐에서 다음 항목 발행...")
            publish_next(publish_to_ig=AUTO_UPLOAD and not DRY_RUN)
        else:
            # 큐 비어있으면 뉴스 자동 수집
            _log("  큐 없음 → 뉴스 자동 수집...")
            selection = collect_and_select()
            _log(f"  주제: {selection.topic}")
            pipeline.run_pipeline(
                topic=selection.topic,
                trend_context=selection.context,
                publish=AUTO_UPLOAD and not DRY_RUN,
                publish_threads=AGENT_THREADS and not DRY_RUN,
                publish_blog=AGENT_BLOG and not DRY_RUN,
                template=AGENT_TEMPLATE,
                fact_check=True,
                auto=True,
            )

        # 텔레그램 완료 알림
        try:
            from src.telegram_bot import notify
            notify("✅ 오늘의 알고 카드뉴스 업로드 완료!")
        except Exception:
            pass

    except Exception:
        _log("[오류] 일일 카드뉴스 작업 실패:")
        traceback.print_exc()
        try:
            from src.telegram_bot import notify
            notify("❌ 오늘 카드뉴스 업로드 실패. 로그를 확인하세요.")
        except Exception:
            pass
    _log("=" * 55)


def job_check_comments() -> None:
    _log("[알고] 댓글 체크...")
    try:
        from src.agents import comment_manager
        replies = comment_manager.run(dry_run=DRY_RUN)
        if replies:
            _log(f"  → {len(replies)}개 답글 완료")
    except Exception:
        traceback.print_exc()


def job_check_dms() -> None:
    _log("[알고] DM 체크...")
    try:
        from src.agents import dm_manager
        replies = dm_manager.run(dry_run=DRY_RUN)
        if replies:
            _log(f"  → {len(replies)}개 답장 완료")
    except Exception:
        traceback.print_exc()


def job_weekly_analysis() -> None:
    """매주 월요일: 성과 분석 + 경쟁 계정 분석."""
    _log("[알고] 주간 분석 시작...")
    try:
        # 성과 분석
        from src.agents.analytics import sync_all_insights, analyze_performance, plot_performance
        from src.persona import load_persona
        _log("  Instagram Insights 동기화...")
        sync_all_insights()
        report = analyze_performance(load_persona())
        _log(f"  성과 분석 완료. 베스트 앵글: {report.best_angle}")
        plot_performance("data/performance_chart.png")

        # 경쟁 계정 분석
        from src.agents.competitor_analyzer import analyze_competitors
        _log("  경쟁 계정 분석...")
        comp_report = analyze_competitors()
        _log(f"  트렌드 주제: {comp_report.top_topics[:3]}")

        # 텔레그램으로 주간 리포트 전송
        summary = (
            f"📊 주간 알고 리포트\n"
            f"베스트 앵글: {report.best_angle}\n"
            f"경쟁사 트렌드: {', '.join(comp_report.top_topics[:3])}\n"
            f"차별화 기회: {', '.join(comp_report.gap_opportunities[:2])}"
        )
        try:
            from src.telegram_bot import notify
            notify(summary)
        except Exception:
            pass
    except Exception:
        _log("[오류] 주간 분석 실패:")
        traceback.print_exc()


# ── 스케줄러 실행 ─────────────────────────────────────────

def start() -> None:
    scheduler = BlockingScheduler(timezone="Asia/Seoul")

    scheduler.add_job(
        job_daily_cardnews,
        CronTrigger(hour=POST_HOUR, minute=0, timezone="Asia/Seoul"),
        id="daily_cardnews", max_instances=1, misfire_grace_time=300,
    )
    scheduler.add_job(
        job_check_comments,
        IntervalTrigger(minutes=COMMENT_MIN),
        id="comment_check", max_instances=1,
    )
    scheduler.add_job(
        job_check_dms,
        IntervalTrigger(minutes=DM_MIN),
        id="dm_check", max_instances=1,
    )
    scheduler.add_job(
        job_weekly_analysis,
        CronTrigger(day_of_week="mon", hour=8, minute=0, timezone="Asia/Seoul"),
        id="weekly_analysis", max_instances=1,
    )

    mode = "[DRY RUN] " if DRY_RUN else ""
    _log(f"{mode}알고 Agent 시작")
    _log(f"  카드뉴스: 매일 {POST_HOUR:02d}:00 | 댓글: {COMMENT_MIN}분 | DM: {DM_MIN}분")
    _log(f"  Threads: {AGENT_THREADS} | 블로그: {AGENT_BLOG} | 업로드: {AUTO_UPLOAD}")
    _log("  종료: Ctrl+C")

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        _log("스케줄러 종료")


if __name__ == "__main__":
    if "--now" in sys.argv:
        job_daily_cardnews()
    elif "--analyze" in sys.argv:
        job_weekly_analysis()
    else:
        start()
