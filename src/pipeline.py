"""
알고 카드뉴스 파이프라인 오케스트레이터
────────────────────────────────────────────────────────
Phase 0: 앵글 선택      — 5가지 마케팅 앵글 중 선택
Phase 1: Trend Analyzer — Tavily 트렌드 수집
Phase 2: Content Creator— GPT-4o 스크립트 생성 + 자기검증
Phase 2.5: Fact Check   — 핵심 수치/사실 교차 검증 (disputed≥2 → 재생성)
Phase 3: Image Searcher — Pexels / DALL-E 배경 이미지
Phase 4: Design Renderer— Pillow 카드 렌더링
Phase 5: Approval       — 사용자 최종 확인 (터미널/텔레그램)
Phase 6: Publisher      — Instagram 캐러셀 자동 업로드
Phase 7: Multi-Platform — Threads / 블로그 동시 발행
"""
from __future__ import annotations

import json as _json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from src.agents import trend_analyzer, content_creator, image_searcher, design_renderer
from src.agents import publisher as ig_publisher
from src.config import NUM_CARDS
from src.persona import Persona, load_persona, resolve_persona

MAX_RETRY = 6   # 기사 5건 교체 + 팩트체크 재시도를 합산해도 여유있도록

# Phase 0: 시사성 높은 주제 키워드 — 자동 앵글 선택 트리거
_ANGLE_AUTO_KEYWORDS = [
    "GPT", "AI", "Claude", "Gemini", "Llama", "Grok",
    "출시", "발표", "공개", "업데이트", "혁신", "혁명",
    "충격", "대란", "논란", "규제", "금지", "차단",
    "인공지능", "반도체", "빅테크", "오픈AI", "앤트로픽",
]


def run_pipeline(
    topic: str,
    num_cards: int | None = None,
    handle: str = "",
    force_dalle: bool = False,
    force_refresh: bool = False,
    save_script: bool = True,
    persona: Persona | None = None,
    publish: bool = False,
    ig_base_url: str = "",
    trend_context: str = "",
    select_angle: bool = False,
    human_approval: bool = False,
    auto: bool = False,
    template: str = "auto",       # auto=주제 자동 감지, dark/light/bold/minimal/gradient
    fact_check: bool = True,      # 팩트체크 레이어 실행 여부
    publish_threads: bool = False, # Threads 동시 발행
    publish_blog: bool = False,   # 블로그 동시 발행
    make_reels: bool = False,     # Reels MP4 생성 여부
    topic_refined: bool = False,  # 이미 정제된 주제 → Phase 0-R 스킵
) -> list[Path]:
    p = persona or load_persona()
    # 주제에 맞는 페르소나 자동 적용 (tone/색상/이모지/해시태그)
    p = resolve_persona(topic, p)
    n = num_cards if num_cards is not None else p.default_count
    h = handle or p.handle

    notes_state: dict = {"last": ""}  # 팩트체크 실패 이유 (재시도 간 전달용)
    ignored_titles: set = set()       # 영상 매칭 불가 시 기사 탈락 목록
    fact_check_retries: int = 0       # 팩트체크 실패로 인한 재시도 횟수
    MAX_FACT_RETRIES = 2              # 같은 기사에 팩트체크를 최대 2번만 재시도

    for retry in range(MAX_RETRY):
        prev_ignored = set(ignored_titles)  # 기사 교체 감지용

        paths = _run_once(
            topic=topic, n=n, h=h, p=p,
            force_dalle=force_dalle,
            force_refresh=force_refresh,
            save_script=save_script,
            publish=publish,
            ig_base_url=ig_base_url,
            trend_context=trend_context,
            select_angle=select_angle,
            human_approval=human_approval,
            auto=auto,
            template=template,
            fact_check=fact_check,
            publish_threads=publish_threads,
            publish_blog=publish_blog,
            make_reels=make_reels,
            retry_num=retry,
            notes_state=notes_state,
            ignored_titles=ignored_titles,
            topic_refined=topic_refined,
        )
        if paths is not None:
            return paths

        # 기사 교체 없이 재시도 = 팩트체크 실패 (같은 기사 재시도)
        if ignored_titles == prev_ignored:
            fact_check_retries += 1
            if fact_check_retries >= MAX_FACT_RETRIES:
                # 팩트체크 재시도 한도 초과 → 현재 기사 포기하고 다음으로
                print(f"  ⚠️ 팩트체크 재시도 {MAX_FACT_RETRIES}회 초과 → 이 기사 포기, 다음 기사 시도")
                if trend_context:
                    break  # trend_context 고정 모드에서는 기사 교체 불가
                # notes_state에 저장된 기사 제목으로 ignored_titles 추가
                failed_title = notes_state.get("last_article_title", "")
                if failed_title:
                    ignored_titles.add(failed_title)
                    print(f"  → '{failed_title[:40]}' 무시 목록 추가")
                notes_state["last"] = ""
                notes_state["last_article_title"] = ""
                fact_check_retries = 0
        else:
            fact_check_retries = 0  # 기사 교체 됐으면 카운터 초기화

    print("  ⚠️ 재생성 최대 횟수 초과.")
    return []


def _run_once(
    topic, n, h, p,
    force_dalle, force_refresh, save_script,
    publish, ig_base_url, trend_context,
    select_angle, human_approval, auto,
    template, fact_check,
    publish_threads, publish_blog,
    make_reels: bool = False,
    retry_num: int = 0,
    notes_state: dict | None = None,
    ignored_titles: set | None = None,
    topic_refined: bool = False,   # True이면 Phase 0-R 스킵 (이미 정제됨)
) -> list[Path] | None:
    from src.agents.angle_selector import select_angle as pick_angle
    from src.agents.approval import wait_for_approval
    from src.agents.design_templates import get_template, get_template_for_topic

    start = datetime.now()
    _sep = "=" * 55
    retry_label = f" (재생성 {retry_num}회차)" if retry_num > 0 else ""
    print(f"\n{_sep}")
    print(f"  알고 파이프라인{retry_label}  |  {p.brand_name} ({h})")
    print(f"  주제: '{topic}'  |  {n}장")
    print(_sep)

    # ── 템플릿 결정 ───────────────────────────────────────
    tmpl_name = get_template_for_topic(topic) if template == "auto" else template
    tmpl = get_template(tmpl_name)
    print(f"\n  템플릿: [{tmpl_name}] {tmpl['name']} — {tmpl.get('description','')}")

    # ── Phase 0-R: 주제 정제 (광범위한 주제 → 단일 기사 집중) ─────
    # topic_refined=True (대시보드에서 이미 정제됨) 또는 trend_context가 있으면 스킵.
    # ⚠️ 주의: 정제 후에도 TrendAnalyzer(Phase 1)는 반드시 실행해야 함.
    #    → Phase 1이 실제 기사 전문 + 영상 후보 검색을 담당하기 때문.
    #    → article_content를 trend_context로 쓰면 Phase 1이 스킵됨 → 내용 부실 + 영상 없음.
    if not topic_refined and not trend_context and retry_num == 0:
        print("\n[0-R] 주제 정제 중...")
        try:
            from src.agents.topic_refiner import refine_topic
            refined, refine_reason, _article_content = refine_topic(topic)
            if refined != topic:
                print(f"  → 주제 변경: '{topic}' → '{refined}'")
                topic = refined   # 이후 모든 단계에 정제된 주제 적용
                # ★ trend_context 설정 금지 ★
                # article_content를 trend_context로 쓰면 TrendAnalyzer가 스킵되어 퀄리티 저하.
                # 정제된 topic으로 TrendAnalyzer가 정상 검색하도록 그대로 둬야 함.
            else:
                print(f"  → 주제 유지: {refine_reason}")
        except Exception as e:
            print(f"  ⚠️ 주제 정제 스킵 ({e})")

    # ── Phase 0: 앵글 선택 ───────────────────────────────
    # select_angle=True 또는 시사성 높은 주제 키워드 감지 시 자동 활성화
    selected_angle = None
    auto_angle = any(kw in topic for kw in _ANGLE_AUTO_KEYWORDS)
    should_select_angle = select_angle or auto_angle

    if should_select_angle:
        reason = "자동 (시사성 높은 주제)" if auto_angle and not select_angle else "수동"
        print(f"\n[0] 마케팅 앵글 선택 중... ({reason})")
        selected_angle = pick_angle(
            topic=topic,
            trend_summary=trend_context or topic,
            persona=p,
            auto=auto,
        )

    # ── Phase 1: Trend Analyzer ──────────────────────────
    if trend_context:
        print("\n[1] 뉴스 컨텍스트 주입...")
        from src.schemas.card_news import TrendReport, TrendResult
        trend_report = TrendReport(
            query=topic,
            results=[TrendResult(title=topic, url="", content=trend_context, score=1.0)],
            summary=trend_context,
        )
    else:
        print("\n[1] 트렌드 분석 중...")
        trend_report = trend_analyzer.run(topic, max_results=5, ignored_titles=ignored_titles)
        if not trend_report.results:
            raise ValueError("해당 주제의 모든 검색된 기사가 검증(영상 누락 등)에서 탈락하여 생성할 수 없습니다.")

        # ── 원문 너무 짧은 기사 즉시 스킵 (1000자 미만 = 제목만 있는 수준) ──────
        # ContentCreator가 없는 내용을 hallucination으로 채우고 팩트체크 전부 실패하는 악순환 방지.
        _main_body_len = len(trend_report.results[0].content) if trend_report.results else 0
        if _main_body_len < 1000:
            print(f"  ⚠️ 원문 너무 짧음 ({_main_body_len}자 < 1000자) → 다음 기사로 교체")
            if ignored_titles is not None and trend_report.results:
                ignored_titles.add(trend_report.results[0].title)
            return None

        print(f"  → 유효 기사 {len(trend_report.results)}건 남음")

    # 이전 팩트체크 실패 이유는 content_creator.run()에 disputed_notes로 직접 전달
    if notes_state and notes_state.get("last") and retry_num > 0:
        print(f"  → 이전 팩트체크 실패 내용 content_creator에 주입 예정")

    # ── Phase 1.5: 영상 후보 풀 수집 (주제 기반 광범위 수집) ──────
    # 이 단계에서는 슬라이드가 아직 없으므로 광범위한 후보 풀만 수집.
    # 실제 슬라이드별 1:1 매핑 + 자막 검증은 Phase 2.6에서 수행.
    video_candidates: list = []   # 후보 풀 (Phase 2.6에서 활용)
    video_infos: list = []        # content_creator 힌트용

    if trend_report.results:
        print(f"\n[1.5] 영상 후보 풀 수집 중 (기사 기반)...")
        try:
            from src.agents.youtube_fetcher import fetch_video_candidates
            video_candidates = fetch_video_candidates(
                article_title=trend_report.results[0].title,
                topic=topic,
                n_keywords=6,   # 키워드 더 많이 (4→6)
                n_per=5,        # 키워드당 영상 더 많이 (3→5)
                days=60,        # 최신 기사라도 60일로 확장
                min_views=2000, # 조회수 기준 완화 (10000→2000): 최신·니치 기사 영상 포함
            )
            video_infos = video_candidates[:4]  # content_creator 힌트용
            print(f"  → {len(video_candidates)}개 후보 수집 완료")
        except Exception as e:
            print(f"  ⚠️ 영상 수집 실패: {e}")

        # try/except 이후 공통 체크 — 예외 발생 OR 정상적으로 0개일 때 모두 처리
        if not video_candidates:
            print(f"  ⚠️ 영상 후보 0개 → 영상 슬라이드 없이 계속 진행합니다.")
            video_infos = []

    # ── Phase 2: Content Creator ─────────────────────────
    print("\n[2] 카드뉴스 스크립트 생성 중 (GPT-4o + 자기검증)...")
    if selected_angle:
        angle_hint = (
            f"\n\n[마케팅 앵글]\n앵글: {selected_angle.angle}\n"
            f"커버 제목(반드시): {selected_angle.cover_title}\n"
            f"캡션 훅(반드시): {selected_angle.hook}"
        )
        trend_report.summary = (trend_report.summary or "") + angle_hint

    # ── Fix A1: 단일 기사 집중 ─────────────────────────────
    # 상위 3건을 혼합하면 서로 다른 주제 기사가 섞여 카드 내용이 분산된다.
    # trend_report.results[0]이 trend_analyzer가 선정한 최고 관련 기사임.
    # 보조 기사는 오직 results[0]과 "동일 기업·제품" 에 관한 것만 추가 허용.
    _main = trend_report.results[0] if trend_report.results else None
    _raw_parts = []
    if _main:
        _raw_parts.append(f"[주 기사: {_main.title}]\n{_main.content}")
        # 보조 기사: 주 기사 제목의 핵심 단어(명사)가 보조 기사 제목에도 있을 때만 포함
        import re as _re
        _main_nouns = set(_re.findall(r'[A-Za-z가-힣]{3,}', _main.title))
        for _aux in trend_report.results[1:3]:
            if not _aux.content.strip():
                continue
            _aux_words = set(_re.findall(r'[A-Za-z가-힣]{3,}', _aux.title))
            _overlap = _main_nouns & _aux_words
            if len(_overlap) >= 2:   # 핵심 단어 2개 이상 겹칠 때만
                _raw_parts.append(f"[보조 기사: {_aux.title}]\n{_aux.content}")
                print(f"  [Pipeline] 보조 기사 포함: '{_aux.title[:40]}' (공통 단어: {_overlap})")
            else:
                print(f"  [Pipeline] 보조 기사 제외: '{_aux.title[:40]}' (주제 불일치)")
    raw_article_body = "\n\n---\n\n".join(_raw_parts)

    script = content_creator.run(
        topic, trend_report, num_cards=n, persona=p,
        video_infos=video_infos,
        raw_article_body=raw_article_body,
        disputed_notes=notes_state.get("last", "") if notes_state else "",
    )
    print(f"  → {len(script.slides)}장 생성 완료")

    if save_script:
        safe = "".join(c if c.isalnum() or c in "가-힣" else "_" for c in topic)[:25]
        Path("data").mkdir(exist_ok=True)
        (Path("data") / f"{safe}_script.json").write_text(
            script.model_dump_json(indent=2), encoding="utf-8"
        )

    # ── Phase 2.5: Fact Check ────────────────────────────
    # 리스트형 주제는 GPT 지식 기반 생성 → 기사와 대조 불가 → 팩트체크 스킵
    from src.agents.content_creator import _is_listicle_topic as _pipeline_is_listicle
    _skip_factcheck = _pipeline_is_listicle(topic)
    if _skip_factcheck:
        print("\n[2.5] 팩트체크 스킵 (리스트형 주제 — GPT 지식 기반 생성)")
    fc_report = None
    disputed_notes = ""
    if fact_check and not _skip_factcheck:
        print("\n[2.5] 팩트체크 중...")
        try:
            from src.agents.fact_checker import check_script
            # 상위 3건 원문 합산 → 더 넓은 교차검증
            source_text = "\n\n".join(
                r.content for r in trend_report.results[:3]
            ) if trend_report.results else ""

            fc_report = check_script(script, source_text=source_text)
            print(f"  → 검증: {fc_report.confirmed}개 확인 / {fc_report.disputed}개 의심 / {fc_report.unverifiable}개 불확실")

            if fc_report.disputed > 0:
                print("  ⚠️ 의심 항목:")
                disputed_items = []
                for item in fc_report.flagged_items:
                    if item.verdict == "disputed":
                        print(f"     - {item.claim} ({item.note})")
                        disputed_items.append(f"• {item.claim[:50]}: {item.note}")
                disputed_notes = "\n".join(disputed_items)

            # disputed 2건 이상 → 재생성 트리거 (다음 retry에 실패 이유 포함)
            if fc_report.disputed >= 2:
                print(f"  ⚠️ disputed {fc_report.disputed}건 — 스크립트 재생성 트리거")
                if notes_state is not None:
                    notes_state["last"] = disputed_notes
                    # 팩트체크 재시도 한도 관리를 위해 현재 기사 제목 기록
                    notes_state["last_article_title"] = (
                        trend_report.results[0].title if trend_report.results else ""
                    )
                return None  # run_pipeline 재시도 루프로 복귀
        except Exception as e:
            print(f"  팩트체크 스킵 (오류: {e})")

    # ── Phase 2.6: 슬라이드별 영상 자막 검증 매핑 ────────────────
    # 슬라이드 생성 후 각 슬라이드 내용과 영상 자막을 GPT로 대조.
    # 자막은 캐시를 통해 중복 다운로드 방지 (429 rate limit 대응).
    # 이 단계에서 확정된 start_seconds는 Phase 4.1에서 그대로 재사용.
    print(f"\n[2.6] 슬라이드별 영상 자막 검증 매핑 중...")
    try:
        from src.agents.youtube_fetcher import find_verified_video_for_slide
        content_slides_list = [s for s in script.slides if s.slide_type == "content"]
        video_infos = []
        used_video_ids: set[str] = set()         # 이미 배정된 video_id (슬라이드별 중복 방지)

        # 리스트형: 기사 후보 풀 무시, 슬라이드 제목 자체로 검색
        if _skip_factcheck:
            assigned_pool: list = []
        else:
            assigned_pool = list(video_candidates)

        # 기사 제목 + 본문 앞부분으로 엔티티 추출 소스 확장
        _main_result = trend_report.results[0] if trend_report.results else None
        _article_title_for_match = (
            f"{_main_result.title} {_main_result.content[:300]}"
            if _main_result and not _skip_factcheck else ""
        )

        for i, slide in enumerate(content_slides_list):
            print(f"  슬라이드{i+1} '{slide.title[:25]}' 영상 검색·자막 검증 중...")
            available_pool = [c for c in assigned_pool if c.video_id not in used_video_ids]

            if _skip_factcheck:
                # 리스트형: 자막 검증 없이 썸네일만 — 429 rate limit 회피
                from src.agents.youtube_fetcher import _search_youtube, _validate_candidates
                # 슬라이드 제목에서 핵심 키워드 추출 (예: "[1/5] DALL-E 이미지 생성" → "DALL-E")
                import re as _re_kw
                _raw_title = _re_kw.sub(r'\[\d+/\d+\]\s*', '', slide.title).strip()
                _kw = f"{topic} {_raw_title}"
                _candidates = _search_youtube(_kw, n=5, days=180)
                _valid = _validate_candidates(_candidates, min_views=2000)
                vi = next(
                    (c for c in _valid if c.video_id not in used_video_ids and c.thumbnail),
                    None
                )
                if vi:
                    vi.start_seconds = 0
                    used_video_ids.add(vi.video_id)
                    if not any(c.video_id == vi.video_id for c in assigned_pool):
                        assigned_pool.append(vi)
                    print(f"  ✓  슬라이드{i+1} 썸네일 확보: '{vi.title[:35]}'")
                    video_infos.append(vi)
                else:
                    print(f"  ⚠️  슬라이드{i+1} '{slide.title[:25]}' 영상 없음 → 이미지 슬라이드로 처리")
                    video_infos.append(None)
            else:
                _entity_src = _article_title_for_match
                vi, start_t = find_verified_video_for_slide(
                    slide_title=slide.title,
                    slide_body=slide.body,
                    topic=topic,
                    candidates=available_pool,
                    used_video_ids=used_video_ids,
                    article_title=_entity_src,
                )
                if vi is not None:
                    vi.start_seconds = start_t
                    used_video_ids.add(vi.video_id)
                    if not any(c.video_id == vi.video_id for c in assigned_pool):
                        assigned_pool.append(vi)
                    video_infos.append(vi)
                else:
                    print(f"  ⚠️  슬라이드{i+1} '{slide.title[:25]}' 영상 없음 → 이미지 슬라이드로 처리")
                    video_infos.append(None)

        video_cnt = sum(1 for v in video_infos if v is not None)
        print(f"  → {video_cnt}/{len(content_slides_list)} 슬라이드 영상 매핑 완료 (영상 없는 슬라이드는 이미지로 처리)")
    except Exception as e:
        # 원인에 상관없이 영상 매핑 실패 → 이 기사 포기, 다음 기사로 교체
        # (예상 밖 오류도 파이프라인을 중단하지 않고 다음 기사 시도)
        print(f"  ⚠️ 영상 매핑 실패({type(e).__name__}: {str(e)[:80]}) → 다음 기사로 교체합니다.")
        if ignored_titles is not None and trend_report.results:
            ignored_titles.add(trend_report.results[0].title)
        return None  # retry loop로 복귀

    # ── Phase 3: Image Searcher ──────────────────────────
    print("\n[3] 배경 이미지 준비 중...")
    bg = image_searcher.get_background_image(
        topic,
        force_dalle=force_dalle,
        force_refresh=force_refresh or retry_num > 0,
        retry_num=retry_num,
    )
    print(f"  → {bg.size[0]}×{bg.size[1]}px 준비 완료")

    # ── Phase 4: Design Renderer ─────────────────────────
    print(f"\n[4] 카드 렌더링 중... (템플릿: {tmpl_name})")

    # 템플릿 색상을 persona에 임시 적용
    from copy import deepcopy
    p_tmpl = deepcopy(p)
    r, g, b = tmpl["accent"]
    p_tmpl.primary_color = "#{:02X}{:02X}{:02X}".format(r, g, b)
    r2, g2, b2 = tmpl["accent2"]
    p_tmpl.accent_color = "#{:02X}{:02X}{:02X}".format(r2, g2, b2)
    p_tmpl.overlay_darkness = tmpl["overlay_alpha"]

    paths = design_renderer.render_card_set(
        script=script, background=bg, handle=h, persona=p_tmpl,
        video_infos=video_infos,
    )

    # ── Phase 2.6 후처리: script.json에 영상 매핑 결과 저장 ──
    if video_infos and paths:
        try:
            out_dir = paths[0].parent
            script_json_path = out_dir / "script.json"
            if script_json_path.exists():
                script_data = _json.loads(script_json_path.read_text(encoding="utf-8"))
                content_indices = [
                    i for i, s in enumerate(script.slides)
                    if s.slide_type == "content"
                ]
                for vi_idx, ci in enumerate(content_indices):
                    if vi_idx < len(video_infos) and video_infos[vi_idx] is not None:
                        v = video_infos[vi_idx]
                        script_data["slides"][ci]["video_id"] = v.video_id
                        script_data["slides"][ci]["video_title"] = v.title
                script_json_path.write_text(
                    _json.dumps(script_data, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                print("  [2.6] script.json 영상 매핑 저장 완료")
        except Exception as e:
            print(f"  ⚠️ script.json 영상 매핑 저장 실패: {e}")

    # ── Phase 4.1: 비디오 슬라이드 병렬 합성 ─────────────────
    if video_infos:
        from src.agents.youtube_fetcher import download_video_snippet, find_best_start_time
        from src.agents.video_renderer import create_video_slide

        content_slides = [s for s in script.slides if s.slide_type == "content"]

        # 처리할 (slide_script, v_info, target_path, target_idx) 목록 수집
        tasks = []
        for i, slide_script in enumerate(content_slides):
            if i >= len(video_infos):
                break
            v_info = video_infos[i]
            if v_info is None:
                continue
            slide_n = slide_script.slide_number
            for p_idx, p_item in enumerate(paths):
                if f"card_{slide_n:02d}_" in p_item.name and p_item.suffix == ".png":
                    tasks.append((slide_script, v_info, p_item, p_idx))
                    break

        def _process_video_slide(args):
            """단일 슬라이드 영상 합성 (ThreadPoolExecutor 내부 실행)"""
            slide_script, v_info, target_path, target_idx = args
            try:
                # Phase 2.6 자막 검증에서 이미 start_seconds 확정됨 → 재사용
                pre_t = getattr(v_info, 'start_seconds', 0)
                if pre_t > 0:
                    best_t = pre_t
                    print(f"  [4.1] 슬라이드 {slide_script.slide_number}: '{v_info.video_id}' → {best_t}초 (자막 검증 완료)")
                else:
                    # fallback: 자막 분석 재시도
                    best_t = find_best_start_time(
                        video_id=v_info.video_id,
                        slide_title=slide_script.title,
                        slide_body=slide_script.body,
                        video_title=v_info.title,
                    )
                    v_info.start_seconds = best_t
                    # 0초 fallback = 자막 없이 시작 → 인트로 스킵 (30초)
                    # 첫 30초는 제목 카드·자기소개·광고가 대부분이므로 스킵
                    if best_t == 0 and v_info.duration > 60:
                        best_t = 30
                        v_info.start_seconds = best_t
                        print(f"  [4.1] 슬라이드 {slide_script.slide_number}: '{v_info.video_id}' → {best_t}초 (인트로 스킵)")
                    else:
                        print(f"  [4.1] 슬라이드 {slide_script.slide_number}: '{v_info.video_id}' → {best_t}초 (재분석)")

                snippet_path = download_video_snippet(
                    v_info.video_id,
                    duration=15,
                    start_time=best_t,
                )
                if snippet_path:
                    out_mp4 = target_path.with_suffix(".mp4")
                    res = create_video_slide(target_path, snippet_path, out_mp4, thumb_ratio=0.45)
                    if res:
                        return target_idx, res, best_t, slide_script.slide_number
            except Exception as e:
                print(f"  ⚠️ 슬라이드 {slide_script.slide_number} 영상 합성 실패: {e}")
            return target_idx, None, 0, getattr(slide_script, 'slide_number', -1)

        if tasks:
            print(f"\n[4.1] 비디오 슬라이드 병렬 합성 중 ({len(tasks)}개)...")
            max_workers = min(4, len(tasks))
            start_time_map: dict[int, int] = {}   # {slide_number: start_seconds}
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {executor.submit(_process_video_slide, t): t for t in tasks}
                for future in as_completed(futures):
                    target_idx, result_path, best_t, slide_num = future.result()
                    if result_path:
                        paths[target_idx] = result_path
                    if best_t > 0 and slide_num > 0:
                        start_time_map[slide_num] = best_t

            # start_seconds를 script.json에 저장 (대시보드 preview에서 활용)
            if start_time_map:
                try:
                    sc_path = next(
                        (p for p in paths if p.parent.is_dir()),
                        None,
                    )
                    if sc_path:
                        sc_json = sc_path.parent / "script.json"
                        if sc_json.exists():
                            sc_data = _json.loads(sc_json.read_text(encoding="utf-8"))
                            for sl in sc_data.get("slides", []):
                                sn = sl.get("slide_number", -1)
                                if sn in start_time_map:
                                    sl["start_seconds"] = start_time_map[sn]
                            sc_json.write_text(_json.dumps(sc_data, ensure_ascii=False, indent=2), encoding="utf-8")
                            print(f"  [4.1] start_seconds → script.json 저장 ({len(start_time_map)}건)")
                except Exception as e:
                    print(f"  ⚠️ start_seconds script.json 저장 실패: {e}")

    # ── Phase 4.5: Reels MP4 생성 ────────────────────────
    if make_reels:
        print("\n[4.5] Reels MP4 생성 중...")
        try:
            from src.agents.video_renderer import render_reels
            reels_path = render_reels(
                card_paths=paths,
                slide_types=[s.slide_type for s in script.slides],
                video_infos=video_infos if video_infos else None,
            )
            if reels_path:
                print(f"  → Reels: {reels_path}")
            else:
                print("  ⚠️ Reels 생성 실패")
        except Exception as e:
            print(f"  ⚠️ Reels 생성 오류: {e}")

    elapsed = (datetime.now() - start).total_seconds()
    print(f"\n{_sep}")
    print(f"  완료! {len(paths)}장 생성 ({elapsed:.1f}초)  →  {paths[0].parent.name}")
    print(_sep)

    # ── Phase 5: 사용자 최종 확인 ────────────────────────
    decision = "upload"
    if human_approval:
        print("\n[5] 최종 확인 — 이미지를 검토해주세요.")
        decision = wait_for_approval(paths, auto=auto)
        if decision == "retry":
            return None
        if decision == "skip":
            print("  업로드 취소.")
            return paths

    # ── Phase 6: Instagram 업로드 ─────────────────────────
    ig_post_id = ""
    if publish and decision == "upload":
        print("\n[6] Instagram 업로드 중...")
        try:
            ig_post_id = ig_publisher.publish(
                image_paths=paths,
                hook=script.hook,
                hashtags=script.hashtags,
                base_url=ig_base_url,
            )
            try:
                from src.agents.publisher import get_post_permalink
                _permalink = get_post_permalink(ig_post_id)
                print(f"  → {_permalink or 'https://www.instagram.com/'}")
            except Exception:
                print(f"  → post_id: {ig_post_id}")
            from src.db import insert_post
            insert_post(
                platform="instagram", topic=topic,
                post_id=ig_post_id,
                angle=selected_angle.angle if selected_angle else "",
                hook=script.hook,
                hashtags=script.hashtags,
                image_dir=str(paths[0].parent),
                posted_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            )
        except Exception as e:
            print(f"  ⚠️ Instagram 업로드 실패: {e}")

    # ── meta.json 저장 (algo-site 투명성 기능용) ──────────
    try:
        _src = trend_report.results[0] if trend_report and trend_report.results else None
        _meta = {
            "topic": topic,
            "source_title": _src.title if _src else "",
            "source_url": _src.url if _src else "",
            "angle": selected_angle.angle if selected_angle else "",
            "fact_confirmed": fc_report.confirmed if fc_report else 0,
            "fact_disputed": fc_report.disputed if fc_report else 0,
            "fact_unverifiable": fc_report.unverifiable if fc_report else 0,
            "generation_seconds": round(elapsed),
            "ig_post_id": ig_post_id,
            "permalink": _permalink if ig_post_id and "_permalink" in dir() else "",
            "posted_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        (paths[0].parent / "meta.json").write_text(
            _json.dumps(_meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"  → meta.json 저장 완료")
    except Exception as e:
        print(f"  ⚠️ meta.json 저장 실패: {e}")

    # ── Phase 7: 멀티 플랫폼 ──────────────────────────────
    if publish_threads and decision == "upload":
        print("\n[7a] Threads 업로드 중...")
        try:
            from src.agents.threads_publisher import publish as threads_pub
            th_post_id = threads_pub(
                image_paths=paths,
                hook=script.hook,
                hashtags=script.hashtags,
                base_url=ig_base_url,
            )
            print(f"  → Threads 업로드 완료: {th_post_id}")
            from src.db import insert_post
            insert_post(
                platform="threads", topic=topic, post_id=th_post_id,
                angle=selected_angle.angle if selected_angle else "",
                hook=script.hook, hashtags=script.hashtags,
                image_dir=str(paths[0].parent),
                posted_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            )
        except Exception as e:
            print(f"  ⚠️ Threads 업로드 실패: {e}")

    if publish_blog and decision == "upload":
        print("\n[7b] 블로그 포스팅 중...")
        try:
            from src.agents.blog_publisher import publish as blog_pub
            blog_result = blog_pub(script=script, image_paths=paths)
            print(f"  → 블로그 발행 완료: {blog_result.get('url', '저장됨')}")
            from src.db import insert_post
            insert_post(
                platform="blog", topic=topic,
                post_id=blog_result.get("url", ""),
                angle=selected_angle.angle if selected_angle else "",
                hook=script.hook, hashtags=script.hashtags,
                image_dir=str(paths[0].parent),
                posted_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            )
        except Exception as e:
            print(f"  ⚠️ 블로그 발행 실패: {e}")

    # algo-site posts_meta.json 자동 갱신
    try:
        import subprocess as _sp
        _exp = Path(__file__).parent.parent / "scripts" / "export_meta.py"
        _sp.run(["python", str(_exp)], check=False)
    except Exception:
        pass

    return paths
