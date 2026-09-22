from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import patch


APP_PATH = Path(__file__).parents[1] / "src" / "dashboard" / "app.py"
APP_SOURCE = APP_PATH.read_text(encoding="utf-8")
APP_TREE = ast.parse(APP_SOURCE)


def _function(name: str) -> ast.FunctionDef:
    for node in APP_TREE.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"dashboard function is missing: {name}")


def _calls(function_name: str) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(_function(function_name)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            names.add(node.func.id)
    return names


def test_queue_page_exposes_only_verified_news_ingestion():
    source = ast.get_source_segment(APP_SOURCE, _function("queue_page")) or ""

    assert "검증되지 않은 주제 등록 차단" in source
    assert 'action="/queue/add"' not in source
    assert 'href="/queue/suggest"' not in source
    assert 'action="/queue/generate"' in source


def test_legacy_manual_queue_endpoint_never_enqueues():
    assert "enqueue" not in _calls("queue_add")
    assert "redirect" in _calls("queue_add")


def test_gpt_suggestion_routes_never_enqueue_or_call_openai():
    assert "enqueue" not in _calls("queue_suggest")
    assert "OpenAI" not in _calls("queue_suggest")
    assert "enqueue" not in _calls("queue_suggest_add")
    assert "redirect" in _calls("queue_suggest")
    assert "redirect" in _calls("queue_suggest_add")


def test_verified_news_route_keeps_v2_bulk_ingestion():
    assert "bulk_generate" in _calls("queue_generate")


def test_queue_page_escapes_html_in_topic():
    # A queue topic now comes from live, arbitrary news headlines rather than
    # a fixed internal list, so a title containing "<"/">" must not be able
    # to inject markup into the operator's dashboard.
    from src.dashboard.app import app

    malicious_row = {
        "id": 1,
        "topic": "<script>alert(1)</script>",
        "status": "pending",
        "scheduled_at": None,
    }
    with patch("src.dashboard.app.get_queue", return_value=[malicious_row]):
        resp = app.test_client().get("/queue")

    assert b"<script>alert(1)</script>" not in resp.data
    assert b"&lt;script&gt;alert(1)&lt;/script&gt;" in resp.data


def test_queue_retry_route_reports_success_and_failure():
    from src.dashboard.app import app

    with patch("src.db.clear_queue_error", return_value=True):
        resp = app.test_client().post("/queue/retry/1", follow_redirects=False)
    assert resp.status_code == 302
    assert "msg=" in resp.headers["Location"]

    with patch("src.db.clear_queue_error", return_value=False):
        resp = app.test_client().post("/queue/retry/1", follow_redirects=False)
    assert resp.status_code == 302
    assert "err=" in resp.headers["Location"]


def test_queue_page_hides_completed_rows_unless_show_all():
    from src.dashboard.app import app

    fake_rows = [
        {"id": 1, "topic": "발행 완료된 것", "status": "published", "scheduled_at": None},
        {"id": 2, "topic": "건너뛴 것", "status": "skipped", "scheduled_at": None},
        {"id": 3, "topic": "대기 중인 것", "status": "pending", "scheduled_at": None},
    ]
    with patch("src.dashboard.app.get_queue", return_value=fake_rows):
        default_resp = app.test_client().get("/queue")
        all_resp = app.test_client().get("/queue?show_all=1")

    default_text = default_resp.data.decode("utf-8")
    all_text = all_resp.data.decode("utf-8")

    assert "대기 중인 것" in default_text
    assert "발행 완료된 것" not in default_text
    assert "건너뛴 것" not in default_text
    assert "전체 보기" in default_text

    assert "대기 중인 것" in all_text
    assert "발행 완료된 것" in all_text
    assert "건너뛴 것" in all_text
    assert "완료 항목 숨기기" in all_text


def test_queue_page_shows_korean_status_labels():
    from src.dashboard.app import app

    fake_rows = [
        {"id": 1, "topic": "대기 항목", "status": "pending", "scheduled_at": None},
        {"id": 2, "topic": "준비 항목", "status": "ready", "scheduled_at": None},
        {"id": 3, "topic": "발행 항목", "status": "published", "scheduled_at": None},
        {"id": 4, "topic": "건너뛴 항목", "status": "skipped", "scheduled_at": None},
    ]
    with patch("src.dashboard.app.get_queue", return_value=fake_rows):
        resp = app.test_client().get("/queue?show_all=1")

    text = resp.data.decode("utf-8")
    assert "대기 중" in text
    assert "준비됨" in text
    assert "발행완료" in text
    assert "건너뜀" in text


def test_current_job_reports_none_when_nothing_is_running():
    from src.dashboard.app import app

    resp = app.test_client().get("/queue/current_job")
    assert resp.get_json() == {"job_id": None}


def test_current_job_reports_the_active_queue_job_and_its_logs():
    import src.dashboard.app as dashboard_app

    dashboard_app._JOBS["job-123"] = {"status": "running", "logs": ["line1", "line2"]}
    dashboard_app._ACTIVE_QUEUE_JOB = {"job_id": "job-123", "mode": "prepare"}
    try:
        resp = dashboard_app.app.test_client().get("/queue/current_job")
        data = resp.get_json()
    finally:
        dashboard_app._ACTIVE_QUEUE_JOB = None
        dashboard_app._JOBS.pop("job-123", None)

    assert data["job_id"] == "job-123"
    assert data["mode"] == "prepare"
    assert data["logs"] == ["line1", "line2"]


def test_direct_dashboard_publish_endpoint_fails_closed():
    from src.dashboard.app import app

    response = app.test_client().post(
        "/publish_now",
        json={"dir_name": "legacy-output", "caption": "unsafe"},
    )
    payload = response.get_json()

    assert response.status_code == 409
    assert payload["success"] is False
    assert payload["error_code"] == "UNSAFE_DIRECT_PUBLISH_BLOCKED"
    assert "ig_publish" not in _calls("publish_now")
