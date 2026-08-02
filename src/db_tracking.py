"""
실행 추적 및 평가 데이터베이스 (P1)
────────────────────────────────────────────────────────
ALGO Agentic Publishing System의 실행 이력, 근거 매핑, 정책 버전을 저장합니다.
"""
from __future__ import annotations
from src.db_factory import get_connection

import os
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

TRACKING_DB_PATH = Path("data/tracking.db")

@contextmanager
def _conn():
    if os.environ.get("ALGO_ENV") == "test":
        try:
            target_path = TRACKING_DB_PATH.resolve()
            prod_tracking = Path("data/tracking.db").resolve()
            prod_algo = Path("data/algo.db").resolve()
            prod_dir = Path("data").resolve()
            
            # 차단 기준: 대상 경로가 운영 DB와 정확히 일치하거나, 운영 data 디렉터리 내부에 있는 경우
            # (os.path.samefile 혹은 Path.resolve() 사용. symlink 완벽 판별은 OS 환경에 따라 한계가 있을 수 있음)
            if target_path == prod_tracking or target_path == prod_algo or prod_dir in target_path.parents:
                print(f"DEBUG: target_path={target_path}, prod_tracking={prod_tracking}, prod_dir={prod_dir}")
                raise RuntimeError("Test environment must not connect to production data/*.db!")
        except RuntimeError:
            raise
        except Exception:
            pass
            
    TRACKING_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = get_connection(str(TRACKING_DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def init_tracking_db() -> None:
    TRACKING_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS content_runs (
            run_id      TEXT PRIMARY KEY,
            topic       TEXT NOT NULL,
            status      TEXT NOT NULL,       -- in_progress, success, failed_factcheck, failed_consistency
            origin      TEXT DEFAULT 'real_pipeline',
            cost_usd    REAL DEFAULT 0.0,
            latency_sec REAL DEFAULT 0.0,
            error_msg   TEXT DEFAULT '',
            strategy_id TEXT DEFAULT '',
            grounded_claim_rate REAL DEFAULT 0.0,
            step_failure_rate REAL DEFAULT 0.0,
            retry_count INTEGER DEFAULT 0,
            created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS agent_steps (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id      TEXT NOT NULL,
            agent_name  TEXT NOT NULL,       -- e.g., NewsCollector, FactChecker, Publisher
            step_desc   TEXT NOT NULL,
            result      TEXT DEFAULT '',
            created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            FOREIGN KEY(run_id) REFERENCES content_runs(run_id)
        );

        CREATE TABLE IF NOT EXISTS sources (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id      TEXT NOT NULL,
            url         TEXT NOT NULL,
            title       TEXT NOT NULL,
            FOREIGN KEY(run_id) REFERENCES content_runs(run_id)
        );

        CREATE TABLE IF NOT EXISTS claims (
            claim_id    TEXT PRIMARY KEY,
            run_id      TEXT NOT NULL,
            slide_idx   INTEGER NOT NULL,
            statement   TEXT NOT NULL,       -- 생성된 문장
            FOREIGN KEY(run_id) REFERENCES content_runs(run_id)
        );

        CREATE TABLE IF NOT EXISTS evidence (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            claim_id    TEXT NOT NULL,
            source_id   INTEGER NOT NULL,
            exact_quote TEXT NOT NULL,       -- 원문 문장
            verdict     TEXT NOT NULL,       -- confirmed, disputed, unverifiable
            confidence  REAL DEFAULT 1.0,
            FOREIGN KEY(claim_id) REFERENCES claims(claim_id),
            FOREIGN KEY(source_id) REFERENCES sources(id)
        );

        CREATE TABLE IF NOT EXISTS quality_checks (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id      TEXT NOT NULL,
            check_type  TEXT NOT NULL,       -- consistency, hallucination, copyright, safety
            passed      INTEGER NOT NULL,    -- 1 (pass), 0 (fail)
            reason      TEXT DEFAULT '',
            FOREIGN KEY(run_id) REFERENCES content_runs(run_id)
        );

        CREATE TABLE IF NOT EXISTS user_edits (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id             TEXT NOT NULL,
            slide_idx          INTEGER NOT NULL,
            original_text      TEXT NOT NULL,       -- 생성 원본
            final_text         TEXT NOT NULL,       -- 사용자가 수정 한 텍스트
            modification_rate  REAL DEFAULT 0.0,    -- 수정률(Levenshtein 거리 등 비율)
            FOREIGN KEY(run_id) REFERENCES content_runs(run_id)
        );

        CREATE TABLE IF NOT EXISTS policy_versions (
            policy_id   TEXT PRIMARY KEY,
            parent_id   TEXT,                -- 이전 정책 (계보)
            status      TEXT DEFAULT 'shadow', -- shadow, canary, active, archived
            strategy    TEXT NOT NULL,       -- JSON (hook, cards, template, etc.)
            created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS metric_snapshots (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id     TEXT NOT NULL,
            snapshot_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            likes       INTEGER DEFAULT NULL,
            comments    INTEGER DEFAULT NULL,
            saves       INTEGER DEFAULT NULL,
            shares      INTEGER DEFAULT NULL,
            reach       INTEGER DEFAULT NULL,
            impressions INTEGER DEFAULT NULL
        );

        CREATE TABLE IF NOT EXISTS content_features (
            run_id      TEXT PRIMARY KEY,
            policy_id   TEXT NOT NULL,
            has_video   INTEGER DEFAULT 0,
            card_count  INTEGER DEFAULT 0,
            color_theme TEXT DEFAULT '',
            FOREIGN KEY(run_id) REFERENCES content_runs(run_id),
            FOREIGN KEY(policy_id) REFERENCES policy_versions(policy_id)
        );

        CREATE TABLE IF NOT EXISTS prompt_versions (
            prompt_id   TEXT PRIMARY KEY,
            agent_name  TEXT NOT NULL,
            template    TEXT NOT NULL,
            version_tag TEXT NOT NULL,
            created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS legacy_experiments (
            exp_id      TEXT PRIMARY KEY,
            policy_id   TEXT NOT NULL,
            status      TEXT DEFAULT 'running', -- running, concluded
            start_time  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            end_time    TEXT,
            conclusion  TEXT,
            FOREIGN KEY(policy_id) REFERENCES policy_versions(policy_id)
        );

        CREATE TABLE IF NOT EXISTS human_feedback (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id             TEXT NOT NULL,
            feedback_type      TEXT NOT NULL,       -- edit, reject, approve
            original_text      TEXT,
            modified_text      TEXT,
            modification_rate  REAL DEFAULT 0.0,
            comment            TEXT,
            created_at         TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            FOREIGN KEY(run_id) REFERENCES content_runs(run_id)
        );

        CREATE TABLE IF NOT EXISTS content_revisions (
            revision_id TEXT PRIMARY KEY,
            content_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            parent_revision_id TEXT,
            revision_number INTEGER NOT NULL,
            content_payload TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            revision_type TEXT NOT NULL,
            actor_type TEXT NOT NULL,
            edit_reason TEXT,
            edit_distance INTEGER,
            prompt_version TEXT,
            policy_version TEXT,
            model_provider TEXT,
            model_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );


        CREATE TABLE IF NOT EXISTS content_revisions (
            revision_id TEXT PRIMARY KEY,
            content_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            revision_number INTEGER NOT NULL,
            content_payload TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            revision_type TEXT NOT NULL,
            actor_type TEXT NOT NULL,
            edit_reason TEXT,
            edit_distance INTEGER,
            prompt_version TEXT,
            policy_version TEXT,
            model_provider TEXT,
            model_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS a_b_experiments (
            experiment_id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            primary_metric TEXT,
            guardrails TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS experiment_assignments (
            assignment_id TEXT PRIMARY KEY,
            experiment_id TEXT NOT NULL,
            assignment_unit_id TEXT NOT NULL,
            assigned_variant TEXT NOT NULL,
            assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS run_reconciliation_events (
            reconciliation_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            previous_status TEXT NOT NULL,
            reconciled_status TEXT NOT NULL,
            detected_at TIMESTAMP NOT NULL,
            reconciliation_reason TEXT,
            artifact_evidence TEXT,
            reconciler_version TEXT
        );
        """)


# ── API ───────────────────────────────────────────────────

def start_run(topic: str) -> str:
    run_id = str(uuid.uuid4())
    with _conn() as conn:
        conn.execute(
            "INSERT INTO content_runs (run_id, topic, status) VALUES (?, ?, ?)",
            (run_id, topic, "in_progress")
        )
    return run_id

def log_step(run_id: str, agent_name: str, step_desc: str, result: str = "") -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT INTO agent_steps (run_id, agent_name, step_desc, result) VALUES (?, ?, ?, ?)",
            (run_id, agent_name, step_desc, result)
        )

def end_run(run_id: str, status: str, cost: float = 0.0, latency: float = 0.0, error: str = "", strategy_id: str = "", grounded_claim_rate: float = 0.0, step_failure_rate: float = 0.0, retry_count: int = 0) -> None:
    with _conn() as conn:
        conn.execute(
            "UPDATE content_runs SET status=?, cost_usd=?, latency_sec=?, error_msg=?, strategy_id=?, grounded_claim_rate=?, step_failure_rate=?, retry_count=? WHERE run_id=?",
            (status, cost, latency, error, strategy_id, grounded_claim_rate, step_failure_rate, retry_count, run_id)
        )

def log_quality_check(run_id: str, check_type: str, passed: bool, reason: str = "") -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT INTO quality_checks (run_id, check_type, passed, reason) VALUES (?, ?, ?, ?)",
            (run_id, check_type, 1 if passed else 0, reason)
        )

def log_user_edit(run_id: str, slide_idx: int, original_text: str, final_text: str) -> None:
    import difflib
    matcher = difflib.SequenceMatcher(None, original_text, final_text)
    modification_rate = 1.0 - matcher.ratio()  
    
    with _conn() as conn:
        conn.execute(
            "INSERT INTO user_edits (run_id, slide_idx, original_text, final_text, modification_rate) VALUES (?, ?, ?, ?, ?)",
            (run_id, slide_idx, original_text, final_text, modification_rate)
        )
def log_source(run_id: str, url: str, title: str) -> int:
    with _conn() as conn:
        cursor = conn.execute(
            "INSERT INTO sources (run_id, url, title) VALUES (?, ?, ?)",
            (run_id, url, title)
        )
        return cursor.lastrowid

def log_claim(claim_id: str, run_id: str, slide_idx: int, statement: str) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT INTO claims (claim_id, run_id, slide_idx, statement) VALUES (?, ?, ?, ?)",
            (claim_id, run_id, slide_idx, statement)
        )

def log_evidence(claim_id: str, source_id: int, exact_quote: str, verdict: str, confidence: float = 1.0) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT INTO evidence (claim_id, source_id, exact_quote, verdict, confidence) VALUES (?, ?, ?, ?, ?)",
            (claim_id, source_id, exact_quote, verdict, confidence)
        )
