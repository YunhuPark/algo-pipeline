# Analytics V2 operations runbook

## Purpose

Analytics V2 records reviewed experiment evidence without changing a publishing policy automatically. It provides deterministic assignment, explicit experiment state transitions, imported 48-hour performance snapshots, editorial feedback metrics, statistical summaries, and human-reviewed recommendation drafts.

Importing an Analytics, API, lifecycle, or state-machine module must not open a database, start a server, call a network service, publish content, or activate a policy.

## Database boundary

All test and synthetic runs require both an explicit environment and an explicit non-production tracking path:

```powershell
$env:ALGO_ENV = "test"
$env:TRACKING_DB_PATH = "$env:TEMP\algo-analytics-test\tracking.db"
```

`src.db_factory.get_connection` rejects repository production paths such as `data/algo.db` and `data/tracking.db` in test mode. The experiment schema is additive and is initialized only by an explicit `init_tracking_db` or `init_experiment_db` call.

Never set `ALLOW_PERSISTENT_DB=true` to make a failing test pass. Stop schedulers, dashboards, and API workers before any reviewed production migration or restoration.

## Experiment control API

The FastAPI control surface exposes:

- `GET /api/experiments`
- `POST /api/experiments/{experiment_id}/transitions`
- `GET /api/experiments/{experiment_id}/metrics`

State-changing requests require a server-side `ADMIN_TOKEN` and a localhost Origin or Referer. The request body cannot choose `actor_type`; the backend derives it. Missing or invalid tokens return 401, missing or invalid origins return 403, invalid transitions and optimistic concurrency conflicts return 409, and unknown request fields return 422.

Allowed experiment transitions are reviewed paths only:

- `DRAFT -> APPROVED`
- `APPROVED -> RUNNING`
- `RUNNING -> PAUSED | COMPLETED | ROLLED_BACK`
- `PAUSED -> RUNNING | COMPLETED | ROLLED_BACK`

Every transition records the previous state, new state, actor, reason, version, and optional idempotency key.

## Metrics and recommendations

Benchmark input includes only `real_pipeline` runs whose status is `SUCCESS`. Synthetic, agent-test contamination, failed, and incomplete records are excluded. A benchmark requires at least 30 total records and 15 records in every compared stratum.

Performance snapshots are imported with an idempotency key and provenance fields. A snapshot is provisional before 48 hours or when reach is unavailable. Negative metrics and timestamps before publication are rejected.

Recommendation generation creates a `DRAFT` only when an adequately sampled candidate has lower average editorial effort than the baseline. Approval and rejection require a non-empty human review reason. Approval records evidence but does not call `activate_policy`, alter allocation, publish content, or contact an external service.

## Agent lifecycle

`AgentStateMachine` and `run_agent_lifecycle` are pure local coordinators. Lifecycle steps are injected callables, run in order, and stop at the first exception. There is no implicit retry. A failed step leaves later steps uncalled and returns `FAILED` with only the exception type, not secret-bearing exception text.

## Safe verification

Run tests with dummy API keys and an isolated tracking DB only:

```powershell
$env:ALGO_ENV = "test"
$env:OPENAI_API_KEY = "test-key"
$env:TAVILY_API_KEY = "test-key"
python -m pytest tests/test_m4_api.py tests/test_experiment_assignment.py -q
python -m pytest tests/test_m5_analytics.py tests/test_analytics_guards.py -q
python -m pytest tests/test_instrumentation.py tests/test_side_effects_guard.py tests/test_lifecycle_guards.py -q
```

Expected behavior:

- all tests use temporary databases;
- publisher, policy activation, allocation mutation, and live HTTP calls remain at zero;
- repository DB/WAL/SHM files remain unchanged;
- recommendation review remains separate from deployment or policy activation.

## Rollback

Rollback is an operator-reviewed Git deployment rollback. Preserve the current tracking database and WAL/SHM files before any database restoration. Keep experiment allocation and automatic publishing disabled until the failure is understood. Do not delete state events, feedback, snapshots, or recommendation reviews to force a previous state.
