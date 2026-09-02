import os
import pytest
import sqlite3
from src.db_factory import get_connection
from fastapi.testclient import TestClient
from unittest.mock import patch
from src.api.app import app

client = TestClient(app)

@pytest.fixture(autouse=True)
def mock_env_and_db(monkeypatch, tmp_path):
    monkeypatch.setenv("ALGO_ENV", "test")
    monkeypatch.setenv("ADMIN_TOKEN", "test_super_secret")
    
    test_tracking_db = tmp_path / "tracking.db"
    test_algo_db = tmp_path / "algo.db"
    
    with patch("src.analytics.db_experiments.TRACKING_DB_PATH", test_tracking_db, create=True), \
         patch("src.analytics.experiments.TRACKING_DB_PATH", test_tracking_db, create=True), \
         patch("src.analytics.assignment.TRACKING_DB_PATH", test_tracking_db, create=True), \
         patch("src.analytics.metrics.TRACKING_DB_PATH", test_tracking_db, create=True), \
         patch("src.analytics.guardrails.TRACKING_DB_PATH", test_tracking_db, create=True), \
         patch("src.api.app.TRACKING_DB_PATH", test_tracking_db, create=True):
         
        # Init DB schema
        with get_connection(test_tracking_db) as conn:
            conn.execute("""
            CREATE TABLE IF NOT EXISTS experiments (
                experiment_id TEXT PRIMARY KEY,
                name TEXT, description TEXT, status TEXT, allocation_percent REAL,
                metric_definition_version TEXT, created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """)
            conn.execute("""
            CREATE TABLE IF NOT EXISTS experiment_state_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                experiment_id TEXT, from_status TEXT, to_status TEXT,
                actor_type TEXT, actor_id TEXT, reason TEXT,
                approval_scope TEXT, idempotency_key TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """)
            conn.execute("""
            CREATE TABLE IF NOT EXISTS experiment_variants (
                variant_id TEXT PRIMARY KEY, experiment_id TEXT,
                is_baseline BOOLEAN, canonical_hash TEXT, config_json TEXT
            )
            """)
            conn.execute("""
            CREATE TABLE IF NOT EXISTS experiment_assignments (
                assignment_id INTEGER PRIMARY KEY AUTOINCREMENT,
                experiment_id TEXT, publication_opportunity_id TEXT,
                variant_id TEXT, canonical_hash TEXT, assigned_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(experiment_id, publication_opportunity_id)
            )
            """)
            conn.execute("CREATE TABLE IF NOT EXISTS content_runs (run_id TEXT PRIMARY KEY, topic TEXT, origin TEXT, latency_sec REAL, cost_usd REAL, error_msg TEXT)")
            
            # Setup a test experiment
            conn.execute("INSERT INTO experiments (experiment_id, name, status, allocation_percent, metric_definition_version) VALUES ('exp_api_1', 'Test API Exp', 'DRAFT', 0.0, 'v1')")
            
        yield test_tracking_db

def test_get_experiments():
    response = client.get("/api/experiments")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["experiment_id"] == "exp_api_1"

def test_transition_no_auth():
    response = client.post("/api/experiments/exp_api_1/transitions", json={
        "target_state": "APPROVED",
        "reason": "Looking good"
    })
    # Should fail due to missing token
    assert response.status_code == 401

def test_transition_invalid_token():
    response = client.post("/api/experiments/exp_api_1/transitions", json={
        "target_state": "APPROVED",
        "reason": "Looking good"
    }, headers={"X-Admin-Token": "wrong_token", "Origin": "http://127.0.0.1:8501"})
    assert response.status_code == 401

def test_transition_no_origin_fails_csrf():
    response = client.post("/api/experiments/exp_api_1/transitions", json={
        "target_state": "APPROVED",
        "reason": "Looking good"
    }, headers={"X-Admin-Token": "test_super_secret"})
    assert response.status_code == 403
    assert "Invalid Origin" in response.json()["detail"]

def test_transition_invalid_origin_csrf():
    response = client.post("/api/experiments/exp_api_1/transitions", json={
        "target_state": "APPROVED",
        "reason": "Looking good"
    }, headers={"X-Admin-Token": "test_super_secret", "Origin": "http://evil.com"})
    
    assert response.status_code == 403
    assert "Invalid Origin" in response.json()["detail"]

def test_transition_success():
    response = client.post("/api/experiments/exp_api_1/transitions", json={
        "target_state": "APPROVED",
        "reason": "Looking good"
    }, headers={"X-Admin-Token": "test_super_secret", "Origin": "http://127.0.0.1:8501"})
    
    assert response.status_code == 200
    assert response.json()["new_state"] == "APPROVED"
    assert response.json()["actor_type"] == "test_human"  # derived from backend securely

def test_transition_invalid_state():
    # Attempting to go APPROVED -> ACCEPTED directly (invalid)
    response = client.post("/api/experiments/exp_api_1/transitions", json={
        "target_state": "ACCEPTED",
        "reason": "Force jump"
    }, headers={"X-Admin-Token": "test_super_secret", "Origin": "http://127.0.0.1:8501"})
    
    assert response.status_code == 409
    assert "Invalid transition" in response.json()["detail"]

def test_transition_optimistic_concurrency_failure():
    # Current version should be 1 (because we created the DRAFT->APPROVED event)
    response = client.post("/api/experiments/exp_api_1/transitions", json={
        "target_state": "RUNNING",
        "reason": "Optimistic check",
        "expected_version": 999  # Stale version
    }, headers={"X-Admin-Token": "test_super_secret", "Origin": "http://127.0.0.1:8501"})
    
    assert response.status_code == 409
    assert "Optimistic concurrency failure" in response.json()["detail"]

def test_idempotency_same_key():
    payload = {
        "target_state": "APPROVED",
        "reason": "Reviewed",
        "idempotency_key": "approve-exp-api-1",
    }
    headers = {
        "X-Admin-Token": "test_super_secret",
        "Origin": "http://127.0.0.1:8501",
    }

    first = client.post(
        "/api/experiments/exp_api_1/transitions", json=payload, headers=headers
    )
    second = client.post(
        "/api/experiments/exp_api_1/transitions", json=payload, headers=headers
    )

    assert first.status_code == 200
    assert first.json()["idempotent"] is False
    assert second.status_code == 200
    assert second.json()["idempotent"] is True
    assert second.json()["new_state"] == "APPROVED"


def test_transition_rejects_actor_override():
    response = client.post(
        "/api/experiments/exp_api_1/transitions",
        json={
            "target_state": "APPROVED",
            "reason": "Looking good",
            "actor_type": "system",
        },
        headers={
            "X-Admin-Token": "test_super_secret",
            "Origin": "http://127.0.0.1:8501",
        },
    )

    assert response.status_code == 422

def test_get_metrics_insufficient_sample():
    # Without any assignments, it should return insufficient_sample=True
    response = client.get("/api/experiments/exp_api_1/metrics")
    assert response.status_code == 200
    assert response.json()["insufficient_sample"] is True
