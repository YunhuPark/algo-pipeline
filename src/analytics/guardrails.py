from __future__ import annotations

from pathlib import Path

from src.analytics.db_experiments import tracking_db_path


TRACKING_DB_PATH: Path | None = None


def should_pause(*, error_rate: float, latency_seconds: float) -> bool:
    """Pure guardrail predicate; callers must review any state transition."""

    return error_rate >= 0.20 or latency_seconds >= 120.0


def configured_db_path() -> Path:
    return Path(TRACKING_DB_PATH) if TRACKING_DB_PATH is not None else tracking_db_path()
