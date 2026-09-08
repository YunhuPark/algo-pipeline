from __future__ import annotations

from pathlib import Path

import pytest

import src.dashboard.app as dashboard


def test_output_directory_resolves_inside_configured_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    output_root = (tmp_path / "output").resolve()
    output_root.mkdir()
    monkeypatch.setattr(dashboard, "OUTPUT_ROOT", output_root)

    assert dashboard._resolve_output_dir("20260908_verified_story") == (
        output_root / "20260908_verified_story"
    )


@pytest.mark.parametrize(
    "unsafe_name",
    ["", ".", "..", "../outside", "folder/child", r"folder\child"],
)
def test_output_directory_rejects_unsafe_segments(unsafe_name: str):
    with pytest.raises(ValueError):
        dashboard._resolve_output_dir(unsafe_name)


def test_review_endpoint_rejects_output_traversal_before_reading_files():
    response = dashboard.app.test_client().post(
        "/generate/review",
        json={"dir_name": "../outside", "decision": "APPROVED"},
    )

    assert response.status_code == 400
    assert response.get_json()["success"] is False


def test_edit_endpoint_rejects_output_traversal_before_llm_call():
    response = dashboard.app.test_client().post(
        "/generate/edit_slide",
        json={
            "dir_name": "../outside",
            "slide_index": 0,
            "instruction": "문장을 다듬어 주세요.",
        },
    )

    assert response.status_code == 400
    assert response.get_json()["success"] is False
