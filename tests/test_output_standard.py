from __future__ import annotations

import json
from pathlib import Path

from src.qa.output_standard import RULES, blocking, check_output

DOC = Path(__file__).resolve().parents[1] / "docs" / "OUTPUT_STANDARD.md"


def test_every_rule_id_is_documented():
    text = DOC.read_text(encoding="utf-8")
    missing = [rule.id for rule in RULES if rule.id not in text]
    assert not missing, f"docs/OUTPUT_STANDARD.md does not mention: {missing}"


def _good_folder(tmp_path):
    from src.agents import design_renderer as dr

    slides = [
        {"slide_number": 1, "slide_type": "cover", "title": "100만 대 이상의 로봇", "body": "후크"},
        {"slide_number": 2, "slide_type": "content", "title": "운영", "body": "아마존은 100만 대 이상의 로봇을 운영합니다.", "visual_values": ["100만 대"]},
        {"slide_number": 3, "slide_type": "content", "title": "노동", "body": "인간 노동자와 함께 일합니다."},
        {"slide_number": 4, "slide_type": "content", "title": "협업", "body": "협업 로봇이 늘고 있습니다."},
        {"slide_number": 5, "slide_type": "cta", "title": "의견은", "body": "댓글로 남겨주세요."},
    ]
    (tmp_path / "script.json").write_text(
        json.dumps({"slides": slides, "source_note": {"outlet": "Ars Technica"}, "hashtags": ["#알고"]}),
        encoding="utf-8",
    )
    layout = [
        {"slide": s["slide_number"], "type": s["slide_type"], "video": False,
         "panel": {"top": dr.TEXT_BODY_TOP, "with_title": False, "title_px": None,
                   "body_px": dr.BODY_FONT_SIZE, "body_lines": 2}
         if s["slide_type"] == "content" else None}
        for s in slides
    ]
    (tmp_path / "layout.json").write_text(json.dumps(layout), encoding="utf-8")
    (tmp_path / "caption.txt").write_text(
        "100만 대 로봇이 일하는 곳이야 🤖\n\n· 첫째 내용이야\n· 둘째 내용이야\n\n@algo\n\n출처: Ars Technica", encoding="utf-8"
    )
    for s in slides:
        (tmp_path / f"card_{s['slide_number']:02d}_{s['slide_type']}.png").write_bytes(b"x")
    return tmp_path


def test_a_conforming_set_has_no_violations(tmp_path):
    assert check_output(_good_folder(tmp_path)) == []


def test_misplaced_body_panel_blocks(tmp_path):
    folder = _good_folder(tmp_path)
    layout = json.loads((folder / "layout.json").read_text(encoding="utf-8"))
    layout[2]["panel"]["top"] = 700
    (folder / "layout.json").write_text(json.dumps(layout), encoding="utf-8")
    violations = check_output(folder)
    assert [v.rule for v in blocking(violations)] == ["S3-position"]


def test_video_without_evidence_and_unverified_stock_clip_block(tmp_path):
    folder = _good_folder(tmp_path)
    script = json.loads((folder / "script.json").read_text(encoding="utf-8"))
    script["slides"][1]["video_id"] = "abc"          # no verified/motion/creator record
    (folder / "script.json").write_text(json.dumps(script), encoding="utf-8")
    (folder / "card_02_content.mp4").write_bytes(b"x")
    (folder / "card_03_content.mp4").write_bytes(b"x")   # stock clip: mp4 but no video_id
    rules = {v.rule for v in blocking(check_output(folder))}
    assert rules == {"S4-video-evidence", "S5-no-unverified-media"}


def test_english_stat_box_on_a_korean_card_warns(tmp_path):
    folder = _good_folder(tmp_path)
    script = json.loads((folder / "script.json").read_text(encoding="utf-8"))
    script["slides"][1]["visual_values"] = ["1 million"]
    (folder / "script.json").write_text(json.dumps(script), encoding="utf-8")
    violations = check_output(folder)
    assert [v.rule for v in violations] == ["S8-stat-language"]
    assert not blocking(violations)


def test_missing_source_blocks(tmp_path):
    folder = _good_folder(tmp_path)
    script = json.loads((folder / "script.json").read_text(encoding="utf-8"))
    script.pop("source_note")
    (folder / "script.json").write_text(json.dumps(script), encoding="utf-8")
    assert "S10-source" in {v.rule for v in blocking(check_output(folder))}


def test_approve_publish_refuses_a_set_that_violates_a_blocking_rule(tmp_path, monkeypatch):
    from src.dashboard import app as dashboard

    folder = _good_folder(tmp_path)
    script = json.loads((folder / "script.json").read_text(encoding="utf-8"))
    script.pop("source_note")                       # S10 block
    (folder / "script.json").write_text(json.dumps(script), encoding="utf-8")
    monkeypatch.setattr("src.db.get_queue_row", lambda _id: {"image_dir": str(folder)})

    started = []
    monkeypatch.setattr(dashboard.threading.Thread, "start", lambda self: started.append(1))
    resp = dashboard.app.test_client().post("/queue/approve_publish", json={"queue_id": 15})
    assert resp.status_code == 422
    assert "S10-source" in resp.get_json()["error"]
    assert not started          # nothing was scheduled to publish
