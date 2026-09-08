from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from src.schemas.card_news import TrendReport, TrendResult
from src.schemas.content_package import PipelineResult
from src.services.generation_service import build_verified_lineage, execute_generation


def test_build_verified_lineage_preserves_multiple_source_urls():
    report = TrendReport(
        query="AI 발표",
        results=[
            TrendResult(
                title="공식 발표",
                url="https://official.example/news",
                content="공식 원문 " * 300,
                score=2.0,
            ),
            TrendResult(
                title="독립 검증 기사",
                url="https://media.example/report",
                content="검증 기사 " * 180,
                score=1.0,
            ),
        ],
    )

    lineage = build_verified_lineage("AI 발표", report)

    assert lineage.is_verified_ready is True
    assert lineage.source_material_level == "full_article"
    assert {item.source_url for item in lineage.evidence_passages} == {
        "https://official.example/news",
        "https://media.example/report",
    }
    assert len({item.article_id for item in lineage.evidence_passages}) == 2


def test_lineage_keeps_primary_source_for_legacy_compatibility():
    report = TrendReport(
        query="테스트",
        results=[
            TrendResult(
                title="주 기사",
                url="https://example.com/a",
                content="근거 본문",
            )
        ],
    )

    lineage = build_verified_lineage("테스트", report)

    assert lineage.source_title == "주 기사"
    assert lineage.source_url == "https://example.com/a"
    assert lineage.evidence_passages[0].article_id == lineage.article_id


def test_human_approval_cannot_be_silently_automatic():
    lineage = build_verified_lineage(
        "테스트",
        TrendReport(
            query="테스트",
            results=[
                TrendResult(
                    title="기사",
                    url="https://example.com/article",
                    content="검증 근거",
                )
            ],
        ),
    )

    with patch("src.services.generation_service._start_tracking") as start:
        with pytest.raises(
            ValueError, match="HUMAN_APPROVAL_REQUIRES_INTERACTIVE_MODE"
        ):
            execute_generation(
                topic="테스트",
                source_lineage=lineage,
                human_approval=True,
                auto=True,
            )

    start.assert_not_called()


def test_execution_persists_original_script_and_source_lineage(tmp_path):
    lineage = build_verified_lineage(
        "감사 가능한 생성",
        TrendReport(
            query="감사 가능한 생성",
            results=[
                TrendResult(
                    title="원문",
                    url="https://example.com/original",
                    content="원문 근거 " * 200,
                )
            ],
        ),
    )
    output_dir = tmp_path / "output" / "item-1"
    output_dir.mkdir(parents=True)
    image_path = output_dir / "card_01_cover.png"
    image_path.write_bytes(b"png")
    script_path = output_dir / "script.json"
    script_path.write_text('{"topic":"감사 가능한 생성"}', encoding="utf-8")
    result = PipelineResult(
        image_paths=[image_path],
        generation_succeeded=True,
        publish_requested=False,
        publish_succeeded=False,
        ig_post_id=None,
        permalink=None,
        failure_stage=None,
        error_code=None,
    )

    with patch("src.pipeline.run_pipeline", return_value=result), patch(
        "src.services.generation_service._start_tracking", return_value="run-1"
    ), patch("src.services.generation_service._record_lineage"), patch(
        "src.services.generation_service._finish_tracking"
    ), patch("src.services.generation_service._record_editorial_review"):
        returned = execute_generation(
            topic="감사 가능한 생성",
            source_lineage=lineage,
        )

    assert returned.run_id == "run-1"
    assert (output_dir / "original_script.json").read_text(encoding="utf-8") == (
        script_path.read_text(encoding="utf-8")
    )
    persisted = json.loads((output_dir / "source_lineage.json").read_text("utf-8"))
    assert persisted["source_url"] == "https://example.com/original"
    meta = json.loads((output_dir / "meta.json").read_text("utf-8"))
    assert meta["run_id"] == "run-1"
    assert meta["editorial_validation_status"] == "ORIGINAL_VERIFIED"
    assert meta["content_revision"] == 0
