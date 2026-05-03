"""
알고 AI Agent — 메인 진입점

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 수동 (주제 지정)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  python main.py "AI 트렌드"
  python main.py "AI 트렌드" --angle --publish --approve
  python main.py "경제 뉴스" --template bold --threads --blog

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 자동 (뉴스 수집 → 주제 선택 → 1회 실행)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  python main.py --auto --angle --publish

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 에이전트 (24시간 자동 루프)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  python main.py --agent --publish
  python main.py --agent --dry-run

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 큐 관리
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  python main.py --queue 5             # 뉴스 5개 미리 생성해서 큐에 쌓기
  python main.py --queue-add "AI 트렌드" # 단일 주제 큐에 추가
  python main.py --queue-publish       # 큐 맨 앞 항목 즉시 발행

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 분석 & 관리
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  python main.py --dashboard           # 웹 대시보드 실행 (localhost:5000)
  python main.py --analyze             # 성과 + 경쟁 계정 분석
  python main.py --templates           # 디자인 템플릿 목록 보기
"""
from __future__ import annotations

import argparse
import io
import os
import re
import sys

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="알고 AI Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # 실행 모드
    p.add_argument("command", nargs="?", help="주제 또는 자연어 지시")
    p.add_argument("--topic", "-t")
    p.add_argument("--auto",           action="store_true", help="뉴스 자동 수집 + 1회 실행")
    p.add_argument("--agent",          action="store_true", help="24시간 에이전트 루프")
    # 생성 옵션
    p.add_argument("--cards", "-n",    type=int, default=None)
    p.add_argument("--handle",         default="")
    p.add_argument("--dalle",          action="store_true")
    p.add_argument("--refresh",        action="store_true")
    p.add_argument("--angle",          action="store_true", help="5가지 앵글 선택")
    p.add_argument("--template",       default="auto",
                   help="디자인 템플릿: auto/dark/light/bold/minimal/gradient")
    p.add_argument("--no-factcheck",   action="store_true", help="팩트체크 스킵")
    p.add_argument("--reels",          action="store_true", help="Reels MP4 생성 (moviepy + yt-dlp)")
    # 업로드
    p.add_argument("--publish",        action="store_true", help="Instagram 업로드")
    p.add_argument("--upload-dir",     metavar="DIR",       help="기존 output 폴더를 재생성 없이 바로 업로드")
    p.add_argument("--threads",        action="store_true", help="Threads 동시 발행")
    p.add_argument("--blog",           action="store_true", help="블로그 동시 발행")
    p.add_argument("--approve",        action="store_true", help="업로드 전 직접 확인")
    p.add_argument("--ig-url",         default="")
    p.add_argument("--dry-run",        action="store_true", help="시뮬레이션 (실제 업로드 안함)")
    # 큐
    p.add_argument("--queue",          type=int, metavar="N", help="N개 미리 생성해서 큐에 쌓기")
    p.add_argument("--queue-add",      metavar="TOPIC",       help="단일 주제 큐에 추가")
    p.add_argument("--queue-publish",  action="store_true",   help="큐 다음 항목 즉시 발행")
    # 분석 & 관리
    p.add_argument("--dashboard",      action="store_true",   help="웹 대시보드 시작")
    p.add_argument("--analyze",        action="store_true",   help="성과 + 경쟁사 분석")
    p.add_argument("--templates",      action="store_true",   help="템플릿 목록 보기")
    return p.parse_args()


def extract_topic(text: str) -> str:
    for pat in [
        r"이번\s*주\s+(.+?)\s*(?:콘텐츠|카드뉴스)?\s*(?:만들|생성)",
        r"(.+?)\s*(?:관련|에\s*대한)\s*(?:카드뉴스|콘텐츠)",
        r"(.+?)\s*카드뉴스\s*(?:만들|생성)",
        r"(.+?)\s*콘텐츠\s*(?:만들|생성)",
    ]:
        m = re.search(pat, text)
        if m:
            return m.group(1).strip()
    return text.strip()


def main() -> None:
    args = parse_args()

    # ── 템플릿 목록 ───────────────────────────────────────
    if args.templates:
        from src.agents.design_templates import get_template_info
        print("\n사용 가능한 디자인 템플릿:")
        for t in get_template_info():
            print(f"  [{t['id']}] {t['name']}  {t['accent_hex']} / {t['accent2_hex']}")
            print(f"         {t['description']}")
        return

    # ── 웹 대시보드 ───────────────────────────────────────
    if args.dashboard:
        port = 5001
        print(f"웹 대시보드 시작: http://localhost:{port}")
        from src.dashboard.app import app
        app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
        return

    # ── 성과 분석 ─────────────────────────────────────────
    if args.analyze:
        from src.agents.analytics import sync_all_insights, analyze_performance, plot_performance
        from src.agents.competitor_analyzer import analyze_competitors
        from src.persona import load_persona
        print("\n[1/3] Insights 동기화...")
        sync_all_insights()
        print("[2/3] 성과 분석...")
        report = analyze_performance(load_persona())
        print(f"  베스트 앵글: {report.best_angle}")
        print(f"  추천 방향: {report.recommendations[:200]}")
        plot_performance("data/performance_chart.png")
        print("[3/3] 경쟁 계정 분석...")
        comp = analyze_competitors()
        print(f"  트렌드 주제: {comp.top_topics[:5]}")
        print(f"  차별화 기회: {comp.gap_opportunities[:3]}")
        print(f"\n전략 제안:\n{comp.recommendations}")
        return

    # ── 큐 관리 ───────────────────────────────────────────
    if args.queue:
        from src.agents.content_queue import bulk_generate, get_status
        print(f"\n큐에 {args.queue}개 자동 생성 중...")
        bulk_generate(count=args.queue, auto_news=True)
        st = get_status()
        print(f"큐 현황: 대기 {st['pending']}개 / 발행됨 {st['published']}개")
        return

    if args.queue_add:
        from src.agents.content_queue import add_topic, get_status
        add_topic(args.queue_add)
        print(f"큐에 추가됨: '{args.queue_add}'")
        st = get_status()
        print(f"큐 현황: 대기 {st['pending']}개")
        return

    if args.queue_publish:
        from src.agents.content_queue import publish_next
        publish_next(publish_to_ig=args.publish and not args.dry_run)
        return

    # ── 기존 폴더 바로 업로드 ────────────────────────────
    if args.upload_dir:
        import json
        from pathlib import Path
        from src.agents import publisher as ig_publisher

        root = Path(__file__).parent
        folder = root / "output" / args.upload_dir
        if not folder.exists():
            # output/ prefix 없이 절대경로로도 허용
            folder = Path(args.upload_dir)
        if not folder.exists():
            print(f"폴더를 찾을 수 없습니다: {args.upload_dir}")
            sys.exit(1)

        script_path = folder / "script.json"
        if not script_path.exists():
            print(f"script.json 없음: {script_path}")
            sys.exit(1)

        script_data = json.loads(script_path.read_text(encoding="utf-8"))
        hook = script_data.get("hook", "")
        hashtags = script_data.get("hashtags", [])
        image_paths = sorted(folder.glob("card_*.png"))

        if not image_paths:
            print("업로드할 카드 이미지(card_*.png)가 없습니다.")
            sys.exit(1)

        print(f"\n[upload-dir] {folder.name}")
        print(f"  이미지 {len(image_paths)}장  |  hook: {hook[:40]}")
        print("  Instagram 업로드 중...")

        try:
            post_id = ig_publisher.publish(
                image_paths=image_paths,
                hook=hook,
                hashtags=hashtags,
                base_url=args.ig_url,
            )
            from src.agents.publisher import get_post_permalink
            permalink = get_post_permalink(post_id)
            print(f"  완료! → {permalink or post_id}")

            # DB 저장 (대시보드 반영)
            from src.db import insert_post
            from datetime import datetime
            insert_post(
                platform="instagram",
                topic=script_data.get("topic", folder.name),
                post_id=post_id,
                angle="",
                hook=hook,
                hashtags=hashtags,
                image_dir=str(folder),
                posted_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            )

            # meta.json 업데이트
            meta_path = folder / "meta.json"
            if meta_path.exists():
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                meta["ig_post_id"] = post_id
                meta["permalink"] = permalink
                meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            print(f"  업로드 실패: {e}")
            sys.exit(1)
        return

    # ── 에이전트 루프 ─────────────────────────────────────
    if args.agent:
        if args.dry_run:   os.environ["AGENT_DRY_RUN"]    = "true"
        if not args.publish: os.environ["AGENT_AUTO_UPLOAD"] = "false"
        if args.threads:   os.environ["AGENT_THREADS"]    = "true"
        if args.blog:      os.environ["AGENT_BLOG"]       = "true"
        if args.template != "auto": os.environ["AGENT_TEMPLATE"] = args.template
        from src.scheduler import start
        start()
        return

    # ── 자동 모드 (1회) ───────────────────────────────────
    if args.auto:
        from src.agents.news_collector import collect_and_select
        from src.pipeline import run_pipeline
        print("\n[알고 Auto] 뉴스 수집 중...")
        sel = collect_and_select()
        print(f"선택 주제: {sel.topic}\n이유: {sel.reason}")
        run_pipeline(
            topic=sel.topic, trend_context=sel.context,
            num_cards=args.cards, handle=args.handle,
            force_dalle=args.dalle, force_refresh=args.refresh,
            publish=args.publish and not args.dry_run,
            publish_threads=args.threads and not args.dry_run,
            publish_blog=args.blog and not args.dry_run,
            ig_base_url=args.ig_url,
            select_angle=args.angle,
            human_approval=args.approve or args.publish,
            template=args.template,
            fact_check=not args.no_factcheck,
            make_reels=args.reels,
        )
        return

    # ── 수동 모드 ─────────────────────────────────────────
    if args.topic:
        topic = args.topic
    elif args.command:
        topic = extract_topic(args.command)
    else:
        print("주제를 입력하세요.\n  예: python main.py \"AI 트렌드\"\n  또는: python main.py --auto")
        sys.exit(1)

    from src.pipeline import run_pipeline
    run_pipeline(
        topic=topic,
        num_cards=args.cards, handle=args.handle,
        force_dalle=args.dalle, force_refresh=args.refresh,
        publish=args.publish and not args.dry_run,
        publish_threads=args.threads and not args.dry_run,
        publish_blog=args.blog and not args.dry_run,
        ig_base_url=args.ig_url,
        select_angle=args.angle,
        auto=True,
        human_approval=args.approve or args.publish,
        template=args.template,
        fact_check=not args.no_factcheck,
        make_reels=args.reels,
    )


if __name__ == "__main__":
    main()
