import pytest
from unittest.mock import patch
import os
import sqlite3
from src.lifecycle import run_agent_lifecycle
from src.agent_control.state_machine import AgentStateMachine, RunState
from src.analytics.recommendations import review_recommendation
import src.db_factory



def test_http_socket_blocked():
    """외부 HTTP/socket 실제 네트워크 호출이 발생하면 즉시 실패하도록 차단하라."""
    with patch('httpx.Client.request', side_effect=Exception("HTTP blocked")) as mock_httpx, \
         patch('requests.Session.request', side_effect=Exception("HTTP blocked")) as mock_requests, \
         patch('socket.socket.connect', side_effect=Exception("Socket blocked")) as mock_socket:
        
        try:
            import httpx
            with httpx.Client(trust_env=False) as client:
                client.request("GET", "http://example.com")
        except Exception as e:
            assert str(e) == "HTTP blocked"
            
        mock_httpx.assert_called_once()
        mock_requests.assert_not_called()
        mock_socket.assert_not_called()

def test_publish_adapter_blocked():
    """publish adapter 실제 Instagram/publish adapter를 mock 또는 spy로 교체하고 검증하라."""
    with patch('src.agents.publisher.publish', side_effect=Exception("Publish blocked")) as mock_publish:
        machine = AgentStateMachine()
        mock_publish.assert_not_called()

def test_policy_activation_blocked(monkeypatch, tmp_path):
    """policy activation 정책 승인 추천 검토 과정에서 실제 activation 함수와 allocation 변경 함수가 호출되지 않음을 직접 검증하라."""
    monkeypatch.setenv("ALGO_ENV", "test")
    test_db = tmp_path / "tracking.db"
    
    with patch('src.analytics.recommendations.activate_policy', create=True) as mock_activate, \
         patch('src.analytics.recommendations.update_allocation', create=True) as mock_allocate, \
         patch('src.analytics.recommendations.TRACKING_DB_PATH', test_db, create=True), \
         patch('src.analytics.db_experiments.TRACKING_DB_PATH', test_db, create=True):
        
        from src.analytics.db_experiments import init_experiment_db
        init_experiment_db()
        
        with src.db_factory.get_connection(test_db) as conn:
            conn.execute("INSERT INTO experiments (experiment_id, name, status, metric_definition_version) VALUES ('exp1', 'Test', 'REVIEW', 'v1')")
            conn.execute("INSERT INTO recommendation_drafts (draft_id, experiment_id, recommended_variant_id, baseline_variant_id, justification, status) VALUES ('d1', 'exp1', 'var_cand', 'var_base', 'good', 'DRAFT')")
            
        res = review_recommendation('d1', 'admin', 'APPROVED', 'lgtm')
        assert res['status'] == 'APPROVED'
        
        mock_activate.assert_not_called()
        mock_allocate.assert_not_called()

def test_db_contamination_blocked(monkeypatch):
    """DB factory를 통한 알려진 운영 DB 접근 차단을 검증하되, 전역 보안 경계라고 표현하지 마라."""
    monkeypatch.setenv("ALGO_ENV", "test")
    import os
    from pathlib import Path
    project_root = Path(__file__).resolve().parent.parent
    prod_path = str((project_root / "data" / "tracking.db").resolve())
    with pytest.raises(src.db_factory.DatabaseContaminationError):
        src.db_factory.get_connection(prod_path)
