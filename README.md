# 알고 — AI 카드뉴스 자동화 파이프라인

매일 오전 9시, AI가 자동으로 AI/테크 트렌드를 수집해 인스타그램 카드뉴스를 생성·발행합니다.

**포트폴리오 사이트:** [algo-site-hazel.vercel.app](https://algo-site-hazel.vercel.app)  
**인스타그램:** [@algo__kr](https://instagram.com/algo__kr)

---

## 전체 구조

```
매일 오전 9시 (Windows Task Scheduler)
        │
        ▼
   run_daily.py
        │
        ├─ 큐에 항목 있음? → 큐에서 발행
        └─ 없음? → GPT가 주제 선택 → 파이프라인 실행
                            │
                            ▼
              1. Tavily API      최신 AI/테크 기사 수집
              2. GPT-4o          앵글 선택 + 6장 스크립트 작성
              3. FactChecker     hallucination 검사 + 교차 검증
              4. DALL-E 3        배경 이미지 생성
              5. Pillow          1080×1350px 카드 렌더링
              6. Instagram API   캐러셀 자동 업로드
              7. export_meta.py  algo-site 대시보드 자동 갱신
```

---

## 파일 구조

```
cardnews/
├── main.py                  # CLI 진입점
├── proxy_router.py          # ngrok 단일 터널 라우터
├── start_services.bat       # Flask + proxy + ngrok 한 번에 시작
├── run_daily.bat            # 레거시 (run_daily.py로 대체됨)
│
├── src/
│   ├── pipeline.py          # 전체 파이프라인 오케스트레이터
│   ├── persona.py           # 브랜드 페르소나 관리
│   ├── db.py                # SQLite 게시 이력
│   ├── dashboard/           # Flask 로컬 관리 대시보드 (localhost:5001)
│   └── agents/
│       ├── trend_analyzer.py     # Tavily 기사 수집
│       ├── content_creator.py    # GPT-4o 스크립트 생성
│       ├── fact_checker.py       # 팩트체크 + 할루시네이션 검증
│       ├── image_searcher.py     # DALL-E 3 이미지 생성
│       ├── design_renderer.py    # Pillow 카드 렌더링
│       ├── publisher.py          # Instagram Graph API 업로드
│       └── content_queue.py      # 예약 큐 관리
│
└── scripts/
    ├── run_daily.py         # Task Scheduler 엔트리포인트
    ├── export_meta.py       # algo-site posts_meta.json 갱신 + git push
    ├── backfill_meta.py     # 기존 output 폴더 meta.json 생성
    ├── backfill_ig_ids.py   # Instagram API로 ig_post_id 백필
    └── capture_dashboard.py # Playwright 대시보드 스크린샷 자동 캡처
```

---

## 실행 방법

### 서비스 시작 (컴퓨터 켤 때)
```bash
start_services.bat
```
Flask 대시보드(5001), proxy_router(9000), ngrok 터널을 한 번에 시작합니다.  
컴퓨터 로그인 시 Task Scheduler가 자동 실행합니다.

### 수동 카드뉴스 생성
```bash
# 주제 지정해서 생성만
python main.py "AI 에이전트의 미래"

# 생성 + 인스타 업로드
python main.py "AI 에이전트의 미래" --publish

# 이미 생성된 카드 바로 업로드
python main.py --upload-dir 20260503_1257_양자컴퓨터의_상장_랠리
```

### 로컬 대시보드
```
http://localhost:5001
```
생성 현황, 큐 관리, 성과 분석, 설정을 웹 UI로 관리합니다.

---

## 환경 변수 (.env)

```
OPENAI_API_KEY=
TAVILY_API_KEY=
IG_ACCESS_TOKEN=
IG_USER_ID=
IG_IMAGE_BASE_URL=https://your-ngrok-domain.ngrok-free.dev
NUM_CARDS=6
```

---

## 기술 스택

| 역할 | 도구 |
|------|------|
| 기사 수집 | Tavily API |
| 스크립트 생성 | OpenAI GPT-4o |
| 이미지 생성 | DALL-E 3 |
| 카드 렌더링 | Python Pillow |
| 발행 | Instagram Graph API |
| 로컬 대시보드 | Flask + SQLite |
| 포트폴리오 사이트 | Next.js 15 + Vercel |
| 자동화 | Windows Task Scheduler |
| 이미지 서빙 | ngrok + proxy_router |
