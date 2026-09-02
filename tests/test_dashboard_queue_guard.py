from __future__ import annotations

import ast
from pathlib import Path


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
