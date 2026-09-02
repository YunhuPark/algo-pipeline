from __future__ import annotations

from pathlib import Path

from src.db_factory import get_connection
from src.db_tracking import resolve_tracking_db_path


TRACKING_DB_PATH: Path | None = None


def tracking_db_path(db_path: str | Path | None = None) -> Path:
    if db_path is not None:
        return Path(db_path)
    if TRACKING_DB_PATH is not None:
        return Path(TRACKING_DB_PATH)
    return resolve_tracking_db_path()


def init_experiment_db(db_path: str | Path | None = None) -> Path:
    """Create the additive experiment schema on an explicitly routed DB."""

    target = tracking_db_path(db_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with get_connection(target) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS experiments (
                experiment_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'DRAFT',
                allocation_percent REAL NOT NULL DEFAULT 0.0,
                metric_definition_version TEXT NOT NULL DEFAULT 'v1',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS experiment_state_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                experiment_id TEXT NOT NULL,
                from_status TEXT NOT NULL,
                to_status TEXT NOT NULL,
                actor_type TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                reason TEXT NOT NULL,
                approval_scope TEXT DEFAULT '',
                idempotency_key TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(experiment_id) REFERENCES experiments(experiment_id)
            );

            CREATE UNIQUE INDEX IF NOT EXISTS ux_experiment_event_idempotency
            ON experiment_state_events(idempotency_key)
            WHERE idempotency_key IS NOT NULL;

            CREATE TABLE IF NOT EXISTS experiment_variants (
                variant_id TEXT PRIMARY KEY,
                experiment_id TEXT NOT NULL,
                is_baseline BOOLEAN NOT NULL DEFAULT 0,
                canonical_hash TEXT NOT NULL,
                config_json TEXT NOT NULL,
                FOREIGN KEY(experiment_id) REFERENCES experiments(experiment_id)
            );

            CREATE TABLE IF NOT EXISTS experiment_assignments (
                assignment_id INTEGER PRIMARY KEY AUTOINCREMENT,
                experiment_id TEXT NOT NULL,
                publication_opportunity_id TEXT NOT NULL,
                variant_id TEXT NOT NULL,
                canonical_hash TEXT NOT NULL DEFAULT '',
                assigned_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(experiment_id, publication_opportunity_id),
                FOREIGN KEY(experiment_id) REFERENCES experiments(experiment_id),
                FOREIGN KEY(variant_id) REFERENCES experiment_variants(variant_id)
            );

            CREATE TABLE IF NOT EXISTS editorial_feedback_events (
                feedback_id INTEGER PRIMARY KEY AUTOINCREMENT,
                content_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                editor_id TEXT NOT NULL,
                approval_decision TEXT NOT NULL,
                edit_reason_category TEXT DEFAULT '',
                text_edit_ratio REAL NOT NULL DEFAULT 0.0,
                claim_correction_count INTEGER NOT NULL DEFAULT 0,
                editorial_effort_score REAL NOT NULL DEFAULT 0.0,
                experiment_id TEXT,
                variant_id TEXT,
                idempotency_key TEXT UNIQUE,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS performance_snapshots (
                snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
                publication_id TEXT NOT NULL,
                measured_at TEXT NOT NULL,
                publication_at TEXT NOT NULL,
                reach INTEGER NOT NULL DEFAULT 0,
                saves INTEGER NOT NULL DEFAULT 0,
                shares INTEGER NOT NULL DEFAULT 0,
                likes INTEGER NOT NULL DEFAULT 0,
                comments INTEGER NOT NULL DEFAULT 0,
                source_type TEXT NOT NULL,
                metric_definition_version TEXT NOT NULL,
                import_idempotency_key TEXT NOT NULL UNIQUE,
                experiment_id TEXT,
                variant_id TEXT,
                is_provisional BOOLEAN NOT NULL DEFAULT 1,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS recommendation_drafts (
                draft_id TEXT PRIMARY KEY,
                experiment_id TEXT NOT NULL,
                recommended_variant_id TEXT NOT NULL,
                baseline_variant_id TEXT NOT NULL,
                candidate_policy_version TEXT DEFAULT '',
                justification TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'DRAFT',
                reviewer_id TEXT,
                review_reason TEXT,
                reviewed_at TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS guardrail_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                experiment_id TEXT NOT NULL,
                guardrail_code TEXT NOT NULL,
                observed_value REAL,
                threshold_value REAL,
                action TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
    return target


def init_tracking_db(db_path: str | Path | None = None) -> Path:
    """Compatibility entrypoint that initializes base and experiment tables."""

    target = tracking_db_path(db_path)
    from src.db_tracking import init_tracking_db as init_base_tracking_db

    init_base_tracking_db(target)
    return target
