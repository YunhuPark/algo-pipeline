# 카드뉴스 출력 기준 (파이프라인 전체 기준)

미리보기가 승인 화면에 올라오기 전에 **모든 세트가 이 기준을 자동으로 통과**해야 한다.
기준 목록의 원본은 `src/qa/output_standard.py`의 `RULES`이고, 아래 표는 그것을 사람이 읽기 쉽게 옮긴 것이다
(`tests/test_output_standard.py`가 두 곳의 규칙 id가 같은지 검사한다).

- **block**: 승인·게시가 막힌다(대시보드가 거절한다).
- **warn**: 승인 화면의 "자동 점검" 패널에 빨간 안내로 뜬다. 승인은 가능하다.
- 점검은 AI를 호출하지 않으므로 비용이 없다. 렌더러가 남긴 `layout.json`, `script.json`, `caption.txt`,
  `background.png`, 카드 파일 이름만 읽는다.

| id | 심각도 | 기준 | 지키는 방법(코드) |
|---|---|---|---|
| S1-structure | block | 표지 1 + 내용 3~6 + CTA 1, 모든 카드 이미지 존재 | `script_assembler`, `check_output` |
| S2-typography | warn | 내용 카드 제목 56px·본문 40px 동일(본문이 길 때만 줄임) | `design_renderer.TITLE_FONT_SIZE/BODY_FONT_SIZE`, `layout.json` |
| S3-position | block | 영상 없는 카드 본문 패널 `TEXT_BODY_TOP`, 영상 카드 `BODY_TOP`(영상 56% 바로 아래) | `design_renderer`, `tests/test_body_grid.py` |
| S4-video-evidence | block | 영상 카드는 자막 검증·움직임 확인·채널명·영상 파일 모두 있음 | `youtube_fetcher.find_verified_video_for_slide`, `pipeline` |
| S5-no-unverified-media | block | 검증 기록 없는 영상(스톡 포함)은 금지, 없으면 문장 카드 | `STOCK_VIDEO_FALLBACK` 기본 off, 자막 없는 영상 거부 |
| S6-cover-figure | warn | 표지 제목의 숫자는 대상이 붙은 구절("100만 대 이상의 로봇")이어야 하고 "80%"처럼 숫자만 있으면 안 됨 | `script_assembler.has_complete_figure`, `_lead_figure_phrase` |
| S7-caption | block | 온전한 첫 줄 + 출처 줄 | `design_renderer._generate_caption`, `caption_guard`, `preview_audit` |
| S8-stat-language | warn | 수치 상자 표기는 본문과 같은 언어 | `script_assembler._localized_stat` |
| S9-background | warn | 본문 카드 배경 평균 밝기 ≤ 125/255 | `design_renderer._apply_background` |
| S10-source | block | 출처 정보 기록 → 카드 하단·캡션에 표시 | `source_note`, `_draw_source_footer` |

## 영상 정책 (한 줄 요약)
영상은 **자막으로 내용이 맞고 + 화면이 실제로 움직이고 + 광고/투자 선동이 아닐 때만** 붙인다.
하나라도 못 지키면 그 카드는 영상 없이 **문장 카드**로 나간다(대체 영상 없음). 영상 카드 비율은 56:44.

## 이 기준을 바꾸는 방법
1. `src/qa/output_standard.py`의 `RULES`와 `check_output`을 먼저 고친다.
2. 이 문서의 표를 같이 고친다(테스트가 id 불일치를 잡는다).
3. 렌더링 코드를 고치고 `tests/test_output_standard.py`, `tests/test_body_grid.py`가 통과하는지 확인한다.
새 규칙을 코드에 먼저 넣고 기준에 안 올리면, 같은 문제가 다시 눈으로만 발견된다.
