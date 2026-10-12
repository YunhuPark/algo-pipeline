from __future__ import annotations

import json

from src.qa.preview_audit import audit_output, caption_issues

GOOD = "Maia 300 칩이 메가와트당 40% 더 처리해\n\n· 첫째 내용이야\n· 둘째 내용이야\n\n@algo__kr"
BROKEN = "300! 미래의 AI가 다가온다 🚀\n\n· 첫째 내용이야\n· 둘째 내용이야\n\n@algo__kr"


def test_broken_opener_is_detected_and_good_one_passes():
    assert caption_issues(GOOD) == []
    assert any("첫 줄" in i for i in caption_issues(BROKEN))


def _folder(tmp_path, caption, slides, mp4s=()):
    (tmp_path / "caption.txt").write_text(caption, encoding="utf-8")
    (tmp_path / "script.json").write_text(
        json.dumps({"slides": slides, "source_note": {"outlet": "The Verge"}}), encoding="utf-8"
    )
    for n in mp4s:
        (tmp_path / f"card_{n:02d}_content.mp4").write_bytes(b"x")
    return tmp_path


def test_broken_first_line_is_replaced_by_the_verified_hook(tmp_path):
    out = _folder(tmp_path, BROKEN, [{"slide_number": 3}])
    report = audit_output(out, script_hook="AI 칩 Maia 300의 모든 것")
    assert (out / "caption.txt").read_text(encoding="utf-8").startswith("⚡ AI 칩 Maia 300의 모든 것")
    assert report["fixed"] and report["ok"]
    assert (out / "audit.json").exists()


def test_stock_clip_and_verified_video_are_reported_differently(tmp_path):
    out = _folder(
        tmp_path,
        GOOD,
        [
            {"slide_number": 2},
            {
                "slide_number": 5, "video_id": "abc", "video_title": "Maia talk",
                "video_creator": "MSFT", "video_verified": True,
            },
        ],
        mp4s=(2, 5),
    )
    report = audit_output(out, script_hook="x")
    assert any("카드 02" in a and "스톡" in a for a in report["attention"])
    assert any("카드 05 영상" == c["item"] and "자막으로 내용 검증됨" in c["result"] for c in report["checks"])
    assert not report["ok"]  # a stock clip needs a human look


def test_video_without_creator_or_file_is_flagged(tmp_path):
    out = _folder(tmp_path, GOOD, [{"slide_number": 4, "video_id": "abc"}], mp4s=())
    report = audit_output(out, script_hook="x")
    assert any("카드 04" in a and "영상 파일이 없습니다" in a for a in report["attention"])


def test_video_without_a_verification_record_is_never_called_verified(tmp_path):
    out = _folder(
        tmp_path,
        GOOD,
        [{"slide_number": 5, "video_id": "abc", "video_title": "t", "video_creator": "c"}],
        mp4s=(5,),
    )
    report = audit_output(out, script_hook="x")
    assert any("카드 05" in a and "검증 기록이 없습니다" in a for a in report["attention"])
    assert not any("검증됨" in c["result"] for c in report["checks"])
