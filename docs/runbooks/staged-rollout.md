# Staged rollout runbook

## Purpose

This runbook moves the recovered pipeline from a verified Git commit to unattended publication without enabling every side effect at once. A successful phase does not authorize the next phase automatically.

## Phase 0: freeze the code baseline

Stop the Scheduler, dashboard, Windows login task, API worker, and every `main.py` process. Confirm a clean `main` checkout and run the isolated suite:

```powershell
git status --short --untracked-files=all
git branch --show-current
git rev-parse HEAD
$env:ALGO_ENV = "test"
$env:OPENAI_API_KEY = "test-key"
$env:TAVILY_API_KEY = "test-key"
python -m pytest tests -q
```

Do not continue with a dirty tree or a failing test.

## Phase 1: migrate the Queue database

Keep every application process stopped. Follow `queue-lineage-v2-migration.md` and retain the exact backup path printed by the runtime command.

```powershell
$env:ALGO_ENV = "production"
python -m src.queue_runtime --db data/algo.db
```

Run the command a second time. It must report that no backup is required because the schema is already current. Do not restore or remove a backup while an application process is running.

## Phase 2: non-publishing queue ingestion

Keep automatic upload disabled:

```powershell
$env:ALGO_ENV = "production"
$env:AGENT_AUTO_UPLOAD = "false"
$env:AGENT_DRY_RUN = "true"
python main.py --queue 1
```

This phase may call the configured news and LLM providers and may add one attested row to the production Queue. It must not call Instagram. If no verified evidence is collected, the command must fail without adding a publishable row.

Review the Queue in the local dashboard or with a reviewed read-only SQLite inspection. Confirm schema version 2, an allowed collection method, non-empty evidence, and a lineage hash. Do not edit these fields manually.

## Phase 3: one supervised publication

Keep the Scheduler and login task disabled. Publish exactly one reviewed pending Queue item from an interactive console:

First follow `instagram-auth-setup.md`, including rotation of any previously exposed
app secret or access token. Then run the read-only remote account preflight:

```powershell
python scripts/ig_preflight.py
```

The preflight must succeed without printing `IG_ACCESS_TOKEN`, changing the database,
uploading media, or creating a container. It verifies that the token resolves to the
configured `IG_USER_ID` before the Queue is dequeued. `IG_IMAGE_BASE_URL` must be an
explicit HTTPS base URL. Using
`IG_IMAGE_BASE_URL=catbox` is a separate, explicit approval to upload every generated
image to a third-party public service. An empty value must fail before dequeue and
before a durable publish attempt is recorded.

```powershell
$env:ALGO_ENV = "production"
python main.py --queue-publish --publish
```

Success requires all of the following:

- one Instagram carousel exists on the intended account;
- the Queue row has a non-empty matching `ig_post_id`;
- `publish_attempt_state` is `CONFIRMED`;
- Queue status is `published`;
- no second post was created;
- no uncertain row was retried.

If the command returns an empty ID, throws after the remote call, or cannot persist the ID, stop. Reconcile Instagram and the local row manually according to the Queue runbook. Never clear attempt fields to retry.

## Phase 4: observation window

Keep unattended publication disabled for at least one reviewed cycle. Check application logs, Queue errors, Quality Gate failures, remote IDs, and Analytics snapshot provenance. Recommendation drafts may be reviewed, but approval must not activate a policy or change allocation.

## Phase 5: unattended automation

Only after Phases 0–4 are accepted, set all three values explicitly in the environment used by Windows Task Scheduler or the login task:

```dotenv
ALGO_ENV=production
AGENT_AUTO_UPLOAD=true
AGENT_DRY_RUN=false
```

Re-enable only one automation entry point first. Do not run APScheduler, the daily task, and the login task concurrently. Confirm the next scheduled execution before enabling another service.

If any variable is missing, malformed, or not a live-production combination, unattended publication must remain disabled and pending Queue rows must be preserved.

## Rollback and stop conditions

Immediately stop automation on checksum mismatch, invalid lineage, Quality Gate failure spikes, stale/unknown attempts, ID persistence failure, duplicate remote content, DB integrity failure, or unexpected external calls.

Preserve current DB, WAL/SHM, logs, the pre-migration backup, and the exact Git SHA. Roll back code only through a reviewed Git deployment. Do not delete evidence, attempt state, analytics events, or recommendation reviews to make the system appear healthy.
