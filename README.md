# 알고 — Evidence-bound AI 카드뉴스 파이프라인

AI/테크 뉴스를 수집해 출처 근거가 있는 카드뉴스를 생성하고, 사람의 편집·승인과 Instagram 성과를 다음 품질 실험으로 연결하는 로컬 운영 시스템입니다. 핵심은 단순 생성이 아니라 **근거 기반 생성 → 인간 검토 → 성과 측정 → 개선 제안**의 폐루프입니다.

**포트폴리오:** [algo-site-hazel.vercel.app](https://algo-site-hazel.vercel.app)

**Instagram:** [@algo__kr](https://instagram.com/algo__kr)

## 현재 안전 계약

- Queue Lineage V2가 출처 metadata·schema version·lineage hash를 검증합니다.
- Fact Checker V2가 근거에 연결된 claim만 렌더링과 게시 단계로 전달합니다.
- 대시보드 수정본은 원문에 없는 수치를 결정적으로 차단하고 Semantic Critic을 다시 통과해야 저장됩니다.
- 각 생성 폴더에 최초 `script.json`과 `source_lineage.json`을 보존해 수정 전후 비교가 가능합니다.
- publisher 호출 전에 durable attempt를 DB에 기록합니다.
- 빈 원격 ID, 저장 실패, stale attempt는 자동 재시도하지 않습니다.
- Analytics V2의 추천은 검토용 초안이며 게시 정책을 자동 변경하지 않습니다.
- 주간 품질 회고는 실제 파이프라인 데이터만 집계하고, 표본이 부족하면 `INSUFFICIENT_DATA`로 종료합니다.
- 회고가 만든 실험 제안은 `DRAFT`이며 사람 승인 전에는 정책·할당을 변경하지 않습니다.
- 기본 `brand` 템플릿은 Algo의 보라/시안 팔레트를 고정하며, 주제별 템플릿 변경은 명시적인 실험으로만 비교합니다.
- 자동 게시의 기본값은 **비활성화**입니다.

직접 주제 게시, legacy queue, cached output 직접 업로드, dashboard 직접 게시처럼 durable attempt를 우회하는 경로는 차단되어 있습니다.

## 처리 흐름

```text
뉴스 수집 → 다중 출처 Queue V2 attestation → Claim 생성 → 결정론 검증
→ Semantic Critic → Script 조립 → 렌더링 → 근거 재검증을 거친 사람 편집·승인 기록
→ Queue publish attempt → Instagram → 48시간 성과 snapshot
→ 주간 품질 회고 → 승인 대기 실험 초안 1건
```

## 주요 구성

```text
main.py                         CLI 진입점
src/pipeline.py                 생성·검증·게시 orchestration
src/agents/content_queue.py     Queue V2 등록과 안전한 게시 상태 머신
src/qa/                         claim 생성·결정론 검증·semantic critic
src/analytics/                  실험 분석과 검토형 recommendation
src/analytics/weekly_review.py  주간 품질·편집·성과 결정론 집계
src/services/                   dashboard/CLI/queue 공통 실행 경로
src/api/                        실험 제어 API
src/dashboard/                  로컬 관리 dashboard
src/queue_runtime.py            DB backup 및 checksum migration
src/automation_mode.py          무인 자동화 fail-closed 설정
docs/runbooks/                  migration·검증·rollout 절차
tests/                          격리된 회귀·부작용 방지 테스트
```

## 설치

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
```

`.env`의 실제 키와 토큰은 Git에 추가하지 마세요. 기본 자동화 설정은 다음과 같이 게시를 차단합니다.

```dotenv
ALGO_ENV=production
AGENT_AUTO_UPLOAD=false
AGENT_DRY_RUN=true
```

## 안전 검증

테스트는 운영 DB가 아닌 임시 DB만 사용합니다.

```powershell
$env:ALGO_ENV = "test"
$env:OPENAI_API_KEY = "test-key"
$env:TAVILY_API_KEY = "test-key"
python -m pytest tests -q
```

## 운영 준비

Scheduler, dashboard, 로그인 작업, `main.py` 프로세스를 모두 종료한 뒤 Queue V2 migration을 실행합니다.

```powershell
$env:ALGO_ENV = "production"
python -m src.queue_runtime --db data/algo.db
```

기존 DB는 변경 전에 `data/backups/` 아래에 일관된 SQLite backup이 생성됩니다. 자세한 검증과 rollback 절차는 [Queue migration runbook](docs/runbooks/queue-lineage-v2-migration.md)을 따르세요.

## 실행 모드

검증된 뉴스 한 건을 Queue에 등록하되 게시하지 않습니다.

```powershell
python main.py --queue 1
```

검토 후 Queue의 다음 한 건을 감독하에 게시합니다.

```powershell
python main.py --queue-publish --publish
```

직접 주제 생성도 먼저 원문과 보조 출처를 수집하며 Instagram 게시는 차단됩니다.

```powershell
python main.py "AI 에이전트의 미래"
```

주간 품질 회고를 수동 실행합니다. 이 명령은 게시·정책 변경·실험 활성화를 하지 않습니다.

```powershell
python scripts/run_weekly_quality_review.py
```

대시보드의 `품질 회고` 메뉴에서도 같은 집계를 실행하고 결과를 볼 수 있습니다. `python -m src.scheduler`를 실행해 둔 경우에는 매주 월요일 08:00에 Instagram 성과를 먼저 동기화하고, `WEEKLY_REVIEW_HOUR`(기본 09:00, Asia/Seoul)에 회고가 자동 실행됩니다. 회고 스케줄은 게시나 정책 변경 권한을 부여하지 않습니다.

성과 동기화는 Meta 응답이 성공한 게시물만 구형 대시보드 테이블과 provenance가 있는 48시간 snapshot에 함께 기록합니다. API 실패를 0 성과로 저장하지 않으며, 같은 시간대 재실행은 idempotency key로 중복 snapshot을 만들지 않습니다.

스케줄러를 계속 띄우지 않고 회고만 즉시 확인하려면 다음 명령을 사용합니다.

```powershell
python -m src.scheduler --quality-review
```

무인 자동화는 staging 검증과 감독 게시가 끝난 후에만 활성화합니다. Windows Task Scheduler와 로그인 작업은 `.env`의 세 값이 모두 명시적으로 live 조건을 만족할 때만 게시합니다.

```dotenv
ALGO_ENV=production
AGENT_AUTO_UPLOAD=true
AGENT_DRY_RUN=false
```

Instagram 자격증명은 [Instagram auth setup](docs/runbooks/instagram-auth-setup.md)을
먼저 완료하고, 전체 전환은 [Staged rollout runbook](docs/runbooks/staged-rollout.md)을
따르세요.

## 운영 제한

- `--queue-add`: 출처 attestation이 없어 차단
- 직접 주제와 `--publish`: Queue V2를 우회하므로 차단
- `--upload-dir`: durable attempt를 우회하므로 차단
- dashboard `/publish_now`: 직접 게시 차단
- uncertain attempt: 자동 reset·자동 retry 금지
- Analytics recommendation 승인: policy activation과 분리
- Weekly review: synthetic run, 실제 파이프라인과 연결되지 않은 게시물, 48시간 미만 provisional 성과 제외
- Metadata export Git push: `AUTO_EXPORT_META_GIT_PUSH=true`일 때만 명시적 실행

## 기술 스택

| 영역 | 기술 |
|---|---|
| 수집·생성 | Tavily, OpenAI |
| 검증 | Pydantic schema, deterministic verifier, semantic critic |
| 렌더링 | Pillow |
| 게시 | Instagram Graph API |
| 저장 | SQLite |
| API·Dashboard | FastAPI, Flask |
| 테스트·CI | pytest, GitHub Actions |
| 자동화 | APScheduler, Windows Task Scheduler |

## 운영 문서

- [Staged rollout](docs/runbooks/staged-rollout.md)
- [Instagram auth setup](docs/runbooks/instagram-auth-setup.md)
- [Queue Lineage V2 migration](docs/runbooks/queue-lineage-v2-migration.md)
- [Fact Checker V2](docs/runbooks/fact-checker-v2.md)
- [Analytics V2](docs/runbooks/analytics-v2.md)
- [Weekly quality review](docs/runbooks/weekly-quality-review.md)
