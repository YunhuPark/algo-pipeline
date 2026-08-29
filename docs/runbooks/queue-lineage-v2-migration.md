# Queue Lineage V2 migration runbook

## Purpose

Queue Lineage V2 permits publication only when a queue row has verified source metadata, an intact lineage hash, and a durable publish attempt. Legacy rows remain visible but are quarantined from publication.

This migration does not use or modify `PRAGMA user_version`. It records the migration ID and checksum in `schema_migrations`.

## Preconditions

1. Stop the scheduler, dashboard, login task, and every `main.py` process.
2. Confirm the repository branch and working tree.
3. Do not delete `data/algo.db`, WAL, or SHM files.
4. Run the migration from the repository root with the production environment configured.

PowerShell:

```powershell
$env:ALGO_ENV = "production"
python -m src.queue_runtime --db data/algo.db
```

The command creates a consistent SQLite backup before changing any existing legacy database. The backup is written under `data/backups/` and its exact path is printed. A new database or an already-current database does not create another backup.

## Expected results

First migration of an existing database:

```text
Queue V2 ready: data/algo.db
Backup: data/backups/algo.pre-queue_lineage_v2.<UTC timestamp>.db
```

Idempotent validation after migration:

```text
Queue V2 ready: data/algo.db
Backup: not required (schema already current or new DB)
```

The following conditions stop startup and publication:

- migration checksum mismatch;
- partial or full untracked Queue V2 columns;
- migration record with missing columns;
- invalid Queue metadata JSON, schema version, collection method, or lineage hash;
- legacy or synthetic queue rows;
- an existing remote post ID or stale publish attempt;
- an uncertain remote publish result.

Do not bypass these failures with `ALTER TABLE`, `git checkout`, or by clearing attempt fields manually.

## Verification

Run the isolated migration and runtime tests. They use temporary databases only.

```powershell
$env:ALGO_ENV = "test"
python -m pytest tests/test_queue_migration.py tests/test_queue_runtime.py -q
python -m pytest tests/test_content_queue_guard.py tests/test_publish_pipeline.py tests/test_production_publish_guard.py -q
```

Expected behavior:

- legacy rows keep their original status and retry count but receive a quarantine error;
- the publisher is called only after `publish_attempt_id` and `publish_started_at` commit;
- a valid remote ID commits before the row becomes `published`;
- `UNCERTAIN_EMPTY_POST_ID` and `REMOTE_PUBLISH_PERSISTENCE_UNCERTAIN` are never auto-retried;
- only the explicit pre-publish allowlist retries, with a maximum of three attempts;
- dashboard and CLI direct-publish endpoints remain blocked.

## Operational commands after migration

Use verified news ingestion and Queue V2 publication:

```powershell
python main.py --queue 1
python main.py --queue-publish --publish
```

The Scheduler, Windows login task, daily script, and `--auto` mode use the same path. `--queue-add`, `--upload-dir`, direct-topic `--publish`, and dashboard `/publish_now` are intentionally blocked.

## Uncertain publish handling

If a row has `publish_attempt_state=UNKNOWN`, `publish_attempt_id`, or `ig_post_id`, do not retry it automatically. First reconcile the Instagram account and logs outside the publisher process. Record the confirmed remote post ID through a reviewed recovery procedure before changing queue state. No automatic attempt-reset command is provided.

## Rollback

Rollback is an operator-controlled recovery, not an application command.

1. Stop all application processes again.
2. Preserve the failed/current database and its WAL/SHM files for investigation.
3. Select the exact backup path printed by the migration command.
4. Restore that backup as `data/algo.db` only after verifying the target and backup paths.
5. Keep Queue publication disabled until the migration failure is understood.

Never restore a backup while the scheduler or dashboard is running.
