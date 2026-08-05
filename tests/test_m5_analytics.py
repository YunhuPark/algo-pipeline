import os
import pytest
from datetime import datetime, timedelta
from src.analytics.db_experiments import init_experiment_db
from src.db_tracking import resolve_tracking_db_path
from src.analytics.feedback import log_editorial_feedback
from src.analytics.import_snapshot import import_performance_snapshot
from src.analytics.benchmark import run_benchmark
from src.analytics.recommendations import generate_recommendation_draft, review_recommendation
from src.db_factory import get_connection

@pytest.fixture(autouse=True)
def mock_env_and_db(monkeypatch, tmp_path):
    monkeypatch.setenv("ALGO_ENV", "test")
    test_tracking_db = tmp_path / "tracking.db"
    from unittest.mock import patch

    with patch("src.analytics.db_experiments.TRACKING_DB_PATH", test_tracking_db, create=True), \
         patch("src.analytics.experiments.TRACKING_DB_PATH", test_tracking_db, create=True), \
         patch("src.analytics.assignment.TRACKING_DB_PATH", test_tracking_db, create=True), \
         patch("src.analytics.metrics.TRACKING_DB_PATH", test_tracking_db, create=True), \
         patch("src.analytics.stats.TRACKING_DB_PATH", test_tracking_db, create=True), \
         patch("src.analytics.feedback.TRACKING_DB_PATH", test_tracking_db, create=True), \
         patch("src.analytics.import_snapshot.TRACKING_DB_PATH", test_tracking_db, create=True), \
         patch("src.analytics.benchmark.TRACKING_DB_PATH", test_tracking_db, create=True), \
         patch("src.analytics.recommendations.TRACKING_DB_PATH", test_tracking_db, create=True), \
         patch("src.db_tracking.TRACKING_DB_PATH", test_tracking_db, create=True):
         
        import src.db_tracking
        src.db_tracking.init_tracking_db()
        init_experiment_db()
        
        # Insert dummy experiment and variants
        with get_connection(test_tracking_db) as conn:
            conn.executescript("""
                INSERT INTO experiments (experiment_id, name, status, metric_definition_version)
                VALUES ('exp_m5', 'Test M5', 'RUNNING', 'v1');
                
                INSERT INTO experiment_variants (variant_id, experiment_id, is_baseline, canonical_hash, config_json)
                VALUES 
                ('var_base', 'exp_m5', 1, 'hash_b', '{}'),
                ('var_cand', 'exp_m5', 0, 'hash_c', '{}');
            """)
            
        yield test_tracking_db

def test_editorial_feedback_logging_and_idempotency(mock_env_and_db):
    eid = log_editorial_feedback(
        content_id="c1", run_id="r1", editor_id="ed1", approval_decision="APPROVED",
        edit_reason_category="factual_correction", text_edit_ratio=0.5,
        claim_correction_count=1, experiment_id="exp_m5", variant_id="var_base",
        idempotency_key="idem_fb_1"
    )
    assert eid > 0
    
    eid2 = log_editorial_feedback(
        content_id="c1", run_id="r1", editor_id="ed1", approval_decision="APPROVED",
        idempotency_key="idem_fb_1"
    )
    assert eid2 == -1 

def test_performance_snapshot_import_and_48h_window(mock_env_and_db):
    now = datetime.now()
    sid1 = import_performance_snapshot(
        "pub1", measured_at=now, publication_at=now - timedelta(days=3),
        reach=0, saves=0, shares=0, likes=0, comments=0,
        source_type="imported_fixture", metric_definition_version="v1",
        import_idempotency_key="idem_snap_1"
    )
    
    sid3 = import_performance_snapshot(
        "pub3", measured_at=now, publication_at=now - timedelta(hours=50),
        reach=1000, saves=10, shares=5, likes=20, comments=2,
        source_type="imported_fixture", metric_definition_version="v1",
        import_idempotency_key="idem_snap_3",
        experiment_id="exp_m5", variant_id="var_base"
    )
    
    with get_connection(mock_env_and_db) as conn:
        conn.row_factory = __import__('sqlite3').Row
        cur = conn.cursor()
        cur.execute("SELECT is_provisional FROM performance_snapshots WHERE snapshot_id=?", (sid1,))
        assert cur.fetchone()['is_provisional'] == 1
        
        cur.execute("SELECT is_provisional FROM performance_snapshots WHERE snapshot_id=?", (sid3,))
        assert cur.fetchone()['is_provisional'] == 0

def test_benchmark_stratified(mock_env_and_db):
    # Insert content_runs to satisfy JOIN condition (real_pipeline, SUCCESS)
    with get_connection(mock_env_and_db) as conn:
        # Create topics A and B
        for i in range(60):
            topic = 'Topic_A' if i < 30 else 'Topic_B'
            conn.execute(f"INSERT INTO content_runs (run_id, topic, status, origin) VALUES ('r{i}', '{topic}', 'SUCCESS', 'real_pipeline')")
            
            # 30 base, 30 cand
            vid = 'var_base' if i % 2 == 0 else 'var_cand'
            
            # cand has lower effort (better)
            effort = 1.0 if vid == 'var_base' else 0.5
            
            conn.execute(f"""
                INSERT INTO editorial_feedback_events (content_id, run_id, experiment_id, variant_id, editorial_effort_score, editor_id, approval_decision) 
                VALUES ('c{i}', 'r{i}', 'exp_m5', '{vid}', {effort}, 'ed1', 'APPROVED')
            """)

    res = run_benchmark("exp_m5", stratify_by="topic_and_variant")
    assert res["status"] == "ready"
    assert res["total_sample"] == 60
    
    keys = list(res["variants"].keys())
    assert "Topic_A::var_base" in keys
    assert "Topic_B::var_cand" in keys
    
    cand_a = res["variants"]["Topic_A::var_cand"]
    assert cand_a["avg_editorial_effort"] == 0.5

def test_benchmark_synthetic_and_contamination_ignored(mock_env_and_db):
    with get_connection(mock_env_and_db) as conn:
        for i in range(100):
            origin = 'agent_test_contamination' if i < 50 else 'synthetic_demo'
            conn.execute(f"INSERT INTO content_runs (run_id, topic, status, origin) VALUES ('rs{i}', 'Synth', 'SUCCESS', '{origin}')")
            conn.execute(f"""
                INSERT INTO editorial_feedback_events (content_id, run_id, experiment_id, variant_id, editorial_effort_score, editor_id, approval_decision) 
                VALUES ('cs{i}', 'rs{i}', 'exp_m5', 'var_cand', 0.1, 'ed1', 'APPROVED')
            """)
            
    res = run_benchmark("exp_m5", stratify_by="variant_id")
    # All ignored -> 0
    assert res["status"] == "insufficient_sample"
    assert res["total_sample"] == 0

def test_statistical_uncertainty(mock_env_and_db):
    # Insert 50 runs for var_base
    with get_connection(mock_env_and_db) as conn:
        for i in range(100):
            vid = 'var_base' if i < 50 else 'var_cand'
            conn.execute(f"INSERT INTO content_runs (run_id, topic, status, origin) VALUES ('ru{i}', 'U', 'SUCCESS', 'real_pipeline')")
            conn.execute(f"""
                INSERT INTO editorial_feedback_events (content_id, run_id, experiment_id, variant_id, editorial_effort_score, editor_id, approval_decision, claim_correction_count) 
                VALUES ('cu{i}', 'ru{i}', 'exp_m5', '{vid}', 2.0, 'ed1', 'APPROVED', 1)
            """)
            
    res = run_benchmark("exp_m5")
    assert res["status"] == "ready"
    
    base_stats = res["variants"]["all_topics::var_base"]
    assert base_stats["factual_correction_rate"] == 1.0
    
    # 50/50 wilson interval for 1.0 rate
    ci_lower, ci_upper = base_stats["factual_correction_ci"]
    assert ci_lower < 1.0
    assert ci_upper == 1.0

def test_recommendation_lifecycle(mock_env_and_db):
    with get_connection(mock_env_and_db) as conn:
        for i in range(100):
            vid = 'var_base' if i < 50 else 'var_cand'
            # Cand has much lower effort -> should be recommended
            effort = 2.0 if vid == 'var_base' else 0.5
            conn.execute(f"INSERT INTO content_runs (run_id, topic, status, origin) VALUES ('rl{i}', 'L', 'SUCCESS', 'real_pipeline')")
            conn.execute(f"""
                INSERT INTO editorial_feedback_events (content_id, run_id, experiment_id, variant_id, editorial_effort_score, editor_id, approval_decision) 
                VALUES ('cl{i}', 'rl{i}', 'exp_m5', '{vid}', {effort}, 'ed1', 'APPROVED')
            """)
            
    draft = generate_recommendation_draft("exp_m5")
    assert draft is not None
    assert draft["status"] == "DRAFT"
    
    draft_id = draft["draft_id"]
    
    # Reject without reason should fail
    with pytest.raises(ValueError):
        review_recommendation(draft_id, actor_id="admin", decision="APPROVED", reason="")
        
    # Approve
    rev = review_recommendation(draft_id, actor_id="admin", decision="APPROVED", reason="Looks good statistically")
    assert rev["status"] == "APPROVED"
    
    # Idempotent
    rev2 = review_recommendation(draft_id, actor_id="admin", decision="APPROVED", reason="Looks good statistically")
    assert rev2["message"] == "Already reviewed (idempotent)"
    
    # Cannot reject if already approved
    with pytest.raises(ValueError):
        review_recommendation(draft_id, actor_id="admin", decision="REJECTED", reason="Changed mind")
        
    # Check DB
    with get_connection(mock_env_and_db) as conn:
        conn.row_factory = __import__('sqlite3').Row
        row = conn.execute("SELECT status, candidate_policy_version, review_reason FROM recommendation_drafts WHERE draft_id=?", (draft_id,)).fetchone()
        assert row["status"] == "APPROVED"
        assert row["candidate_policy_version"] == "hash_c"
        assert row["review_reason"] == "Looks good statistically"
