"""Exercise Flask routes with synthetic files and no provider/network access."""
import importlib
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def dashboard(monkeypatch, tmp_path):
    monkeypatch.setenv("ALGO_ENV", "test")
    monkeypatch.setenv("ALGO_DB_PATH", str(tmp_path / "test-algo.db"))
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic")
    monkeypatch.setenv("TAVILY_API_KEY", "synthetic")
    monkeypatch.setenv("ADMIN_TOKEN", "synthetic-admin")
    module = importlib.import_module("src.dashboard.app")
    monkeypatch.setattr(module, "ROOT", tmp_path)
    (tmp_path / "output" / "safe").mkdir(parents=True)
    (tmp_path / "sentinel.txt").write_text("outside-sentinel")
    (tmp_path / "output" / "safe" / "card.png").write_bytes(b"synthetic-image")
    return module


HEADERS = {"X-Admin-Token": "synthetic-admin"}


@pytest.mark.parametrize("url", ["/output_img/%2e%2e/sentinel.txt", "/output_video/%2e%2e/sentinel.txt",
                                     "/output_img/..%5c/sentinel.txt", "/caption/..%5c",
                                     "/preview/..%5c", "/publish_page/..%5c", "/subtitle/..%5cunsafe"])
def test_file_routes_reject_parent_and_windows_separators(dashboard, url):
    response = dashboard.app.test_client().get(url, headers=HEADERS)
    assert response.status_code in (400, 404)
    assert b"outside-sentinel" not in response.data


def test_public_media_remains_available_but_nonmedia_and_symlinks_are_denied(dashboard, tmp_path):
    client = dashboard.app.test_client()
    assert client.get("/output_img/safe/card.png").data == b"synthetic-image"
    (tmp_path / "output" / "safe" / "private.txt").write_text("secret-sentinel")
    assert client.get("/output_img/safe/private.txt").status_code == 404
    link = tmp_path / "output" / "linked"
    (tmp_path / "sentinel.png").write_bytes(b"outside-image-sentinel")
    try:
        link.symlink_to(tmp_path, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            raise
        # Windows junctions do not need the symlink privilege.
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(tmp_path)],
                       check=True, capture_output=True)
    assert client.get("/output_img/linked/sentinel.png").status_code == 404


def test_basic_browser_auth_requires_origin_for_mutations(dashboard):
    import base64
    authorization = "Basic " + base64.b64encode(b"admin:synthetic-admin").decode()
    client = dashboard.app.test_client()
    headers = {"Authorization": authorization}
    payload = {"persona_json": "{}"}
    assert client.post("/settings/persona", data=payload, headers=headers).status_code == 403
    assert client.post("/settings/persona", data=payload, headers={
        **headers, "Origin": "http://localhost"}).status_code == 302
    assert client.get("/settings", headers={**headers, "Sec-Fetch-Site": "cross-site"}).status_code == 403


def test_admin_routes_require_configured_credentials_and_reject_csrf(dashboard, tmp_path, monkeypatch):
    client = dashboard.app.test_client()
    payload = {"persona_json": '{"handle":"safe"}'}
    assert client.post("/settings/persona", data=payload).status_code == 401
    assert not (tmp_path / "persona.json").exists()
    assert client.post("/settings/persona", data=payload, headers={
        **HEADERS, "Origin": "https://attacker.invalid"}).status_code == 403
    assert client.post("/settings/persona", data=payload, headers=HEADERS).status_code == 302
    assert json.loads((tmp_path / "persona.json").read_text()) == {"handle": "safe"}
    monkeypatch.delenv("ADMIN_TOKEN")
    assert client.post("/settings/persona", data=payload, headers=HEADERS).status_code == 401


def test_edit_request_cannot_escape_output_directory(dashboard):
    response = dashboard.app.test_client().post("/generate/edit_slide", headers=HEADERS, json={
        "dir_name": "../", "slide_index": 0, "instruction": "change"})
    assert response.status_code == 404


def test_slide_edit_updates_script_after_fenced_json_response(dashboard, monkeypatch, tmp_path):
    import langchain_openai
    import src.agents.design_renderer as renderer
    import src.persona as persona
    from PIL import Image
    monkeypatch.setattr(langchain_openai, "ChatOpenAI", lambda **kwargs: SimpleNamespace(
        invoke=lambda _: SimpleNamespace(content='```json\n{"title":"new","body":"body"}\n```')))
    monkeypatch.setattr(persona, "load_persona", lambda: SimpleNamespace(handle="test"))
    monkeypatch.setattr(renderer, "_build_style", lambda p: {})
    monkeypatch.setattr(renderer, "_apply_background", lambda image: image)
    monkeypatch.setattr(renderer, "_render_cover", lambda *a, **k: Image.new("RGB", (2, 2)))
    monkeypatch.setattr(renderer, "_generate_caption", lambda *a: "updated")
    script = {"topic": "topic", "hook": "hook", "hashtags": [], "slides": [
        {"slide_number": 1, "slide_type": "cover", "title": "old", "body": "old"}]}
    path = tmp_path / "output" / "safe" / "script.json"
    path.write_text(json.dumps(script))
    response = dashboard.app.test_client().post("/generate/edit_slide", headers=HEADERS, json={
        "dir_name": "safe", "slide_index": 0, "instruction": "change"})
    assert response.get_json()["success"] is True, response.get_json()
    assert json.loads(path.read_text())["slides"][0]["title"] == "new"
    assert (path.parent / "card_01_cover.png").is_file()
