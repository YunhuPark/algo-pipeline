# 알고 — Evidence-bound AI 카드뉴스 파이프라인

AI/테크 뉴스를 수집해 출처 근거가 있는 카드뉴스를 생성하고, 검증된 Queue를 통해 Instagram 게시까지 수행하는 로컬 자동화 시스템입니다.

**포트폴리오:** [algo-site-hazel.vercel.app](https://algo-site-hazel.vercel.app)

**Instagram:** [@algo__kr](https://instagram.com/algo__kr)

## 현재 안전 계약

- Queue Lineage V2가 출처 metadata·schema version·lineage hash를 검증합니다.
- Fact Checker V2가 근거에 연결된 claim만 렌더링과 게시 단계로 전달합니다.
- publisher 호출 전에 durable attempt를 DB에 기록합니다.
- 빈 원격 ID, 저장 실패, stale attempt는 자동 재시도하지 않습니다.
- Analytics V2의 추천은 검토용 초안이며 게시 정책을 자동 변경하지 않습니다.
- 자동 게시의 기본값은 **비활성화**입니다.

직접 주제 게시, legacy queue, cached output 직접 업로드, dashboard 직접 게시처럼 durable attempt를 우회하는 경로는 차단되어 있습니다.

## 처리 흐름

```text
뉴스 수집 → Queue V2 attestation → Claim 생성 → 결정론 검증
→ Semantic Critic → Script 조립 → 렌더링 → publish attempt 기록
→ Instagram API → remote ID 기록 → published 확정
```

## 주요 구성

```text
main.py                         CLI 진입점
src/pipeline.py                 생성·검증·게시 orchestration
src/agents/content_queue.py     Queue V2 등록과 안전한 게시 상태 머신
src/qa/                         claim 생성·결정론 검증·semantic critic
src/analytics/                  실험 분석과 검토형 recommendation
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

직접 주제 생성은 가능하지만 Instagram 게시는 차단됩니다.

```powershell
python main.py "AI 에이전트의 미래"
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
