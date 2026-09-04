import hashlib
from unittest.mock import MagicMock, patch

import pytest
from pathlib import Path
from src.schemas.content_package import PipelineResult
from src.qa.deterministic_verifier import QualityGateError
from src.qa.publish_quality_gate import validate_publish_quality
from src.schemas.card_news import TrendReport, TrendResult, CardNewsScript, Slide
from src.schemas.fact_check import FactCheckItem, FactCheckReport
from src.schemas.queue_schemas import CollectionMethod, PublishAttemptState, QueueMetadataV2
import src.agents.content_queue as cq

@pytest.fixture
def mock_pipeline_agents(monkeypatch, tmp_path):
    monkeypatch.setenv("ALGO_ENV", "test")
    monkeypatch.setenv("ALGO_DB_PATH", str(tmp_path / "pipeline.db"))
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")
    monkeypatch.chdir(tmp_path)

    with patch("src.agents.topic_refiner.refine_topic") as mock_tr, \
         patch("src.agents.trend_analyzer.run") as mock_ta, \
         patch("src.agents.youtube_fetcher.fetch_video_candidates") as mock_yf1, \
         patch("src.agents.youtube_fetcher.find_verified_video_for_slide") as mock_yf2, \
         patch("src.agents.youtube_fetcher.download_video_snippet") as mock_yf3, \
         patch("src.pipeline.content_creator.ContentCreator") as mock_cc_cls, \
         patch("src.agents.fact_checker.check_script") as mock_fc_check, \
         patch("src.agents.image_searcher.get_background_image") as mock_is, \
         patch("src.agents.design_renderer.render_card_set") as mock_dr, \
         patch("src.agents.approval.wait_for_approval") as mock_app, \
         patch("src.agents.angle_selector.select_angle") as mock_angle, \
         patch("src.db.insert_post") as mock_insert_post, \
         patch("subprocess.run"):

        mock_tr.return_value = ("Test Topic", "Focus", "Reason")
        mock_yf1.return_value = []
        mock_ta.return_value = TrendReport(
            query="Test",
            results=[TrendResult(title="Test", url="http://test.com", content="A" * 1500, score=1.0)],
            summary=""
        )

        mock_script = CardNewsScript(
            topic="Test Topic",
            slides=[
                Slide(slide_number=1, slide_type="cover", title="Test", body="Test"),
                Slide(slide_number=2, slide_type="content", title="Fact", body="Verified fact"),
                Slide(slide_number=3, slide_type="cta", title="Source", body="Read the source"),
            ],
            hook="hook",
            hashtags=[]
        )
        mock_fc = FactCheckReport(
            confirmed_claim_ids=["claim-1"],
            confirmed=1,
        )

        # content_creator.run()은 CardNewsScript만 반환 (tuple이 아님)
        mock_cc = mock_cc_cls.return_value.run
        mock_cc.return_value = mock_script
        mock_cc_cls.return_value.last_fact_check_report = mock_fc
        mock_fc_check.return_value = mock_fc

        # image_searcher.get_background_image()는 PIL Image를 반환
        mock_bg = MagicMock()
        mock_bg.size = (1080, 1080)
        mock_is.return_value = mock_bg
        mock_yf2.return_value = (None, 0)
        mock_dr.return_value = [Path("dummy.png")]
        mock_app.return_value = "upload"

        yield {
            "ta": mock_ta,
            "cc": mock_cc,
            "cc_instance": mock_cc_cls.return_value,
            "is": mock_is,
            "dr": mock_dr,
            "app": mock_app,
            "fc": mock_fc,
            "fc_check": mock_fc_check,
            "script": mock_script
        }


def test_pipeline_does_not_call_deprecated_fact_checker(mock_pipeline_agents):
    """Claim 기반 검증 후 deprecated fact_checker를 다시 호출하지 않는다."""
    from src.pipeline import run_pipeline

    result = run_pipeline(topic="Test", publish=False, auto=True)

    assert result.generation_succeeded is True
    mock_pipeline_agents["fc_check"].assert_not_called()


def test_pipeline_fails_closed_without_fact_check_report(mock_pipeline_agents):
    """ContentCreator가 검증 보고서를 남기지 않으면 렌더·게시 전에 차단한다."""
    from src.pipeline import run_pipeline

    mock_pipeline_agents["cc_instance"].last_fact_check_report = None
    with patch("src.pipeline.ig_publisher.publish") as publisher:
        result = run_pipeline(topic="Test", publish=True, auto=True)

    assert result.generation_succeeded is False
    assert result.failure_stage == "QUALITY_GATE"
    assert result.error_code == "FACT_CHECK_REPORT_INVALID"
    publisher.assert_not_called()

def test_publisher_exception_propagates(mock_pipeline_agents):
    """원격 호출 예외는 재시도 가능한 실패로 추측하지 않고 uncertain으로 보존한다."""
    from src.pipeline import run_pipeline

    before_publish = MagicMock()
    with patch("src.pipeline.ig_publisher.publish", side_effect=Exception("Network Error")):
        res = run_pipeline(
            topic="Test",
            publish=True,
            auto=True,
            publish_attempt_id="attempt-1",
            before_publish=before_publish,
        )
        assert res.publish_succeeded is False
        assert res.failure_stage == "publisher"
        assert res.error_code == "REMOTE_PUBLISH_PERSISTENCE_UNCERTAIN"
        assert res.publish_attempt_state == PublishAttemptState.UNKNOWN
        before_publish.assert_called_once_with("attempt-1")

def test_empty_id_is_failure(mock_pipeline_agents):
    """빈 원격 ID는 성공으로 간주하지 않고 자동 재시도를 차단한다."""
    from src.pipeline import run_pipeline

    with patch("src.pipeline.ig_publisher.publish", return_value=""):
        res = run_pipeline(
            topic="Test",
            publish=True,
            auto=True,
            publish_attempt_id="attempt-2",
            before_publish=MagicMock(),
        )
        assert res.publish_succeeded is False
        assert res.failure_stage == "publisher"
        assert res.error_code == "UNCERTAIN_EMPTY_POST_ID"
        assert res.publish_attempt_state == PublishAttemptState.UNKNOWN


def _attested_row() -> dict:
    metadata = QueueMetadataV2(
        topic="T",
        source_title="Source",
        source_url="https://example.com/source",
        context="verified context",
        evidence=[{"title": "Source", "url": "https://example.com/source"}],
    )
    canonical = metadata.canonical_json()
    return {
        "id": 1,
        "topic": metadata.topic,
        "context": metadata.context,
        "angle_hint": "",
        "image_dir": "",
        "collection_method": CollectionMethod.NEWS_COLLECTOR.value,
        "metadata_schema_version": 2,
        "metadata_json": canonical,
        "lineage_hash": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    }


def _quality_script() -> CardNewsScript:
    return CardNewsScript(
        topic="검증 주제",
        hook="검증된 훅",
        slides=[
            Slide(slide_number=1, slide_type="cover", title="표지", body="표지 본문"),
            Slide(slide_number=2, slide_type="content", title="근거", body="검증된 본문"),
            Slide(slide_number=3, slide_type="cta", title="확인", body="원문을 확인하세요"),
        ],
        hashtags=["#검증"],
    )

def test_queue_does_not_mark_failed_as_published():
    """원격 상태가 불확실하면 published 전환 없이 attempt를 보존한다."""
    with patch("src.agents.content_queue._validate_publish_configuration"), \
         patch("src.agents.content_queue.dequeue_next", return_value=_attested_row()), \
         patch("src.agents.content_queue._run_full_pipeline") as mock_pipeline, \
         patch("src.agents.content_queue.mark_queue_status") as mock_mark, \
         patch("src.agents.content_queue.mark_queue_error") as mock_error:

        mock_pipeline.return_value = PipelineResult(
            image_paths=[Path("a.png")],
            generation_succeeded=True,
            publish_requested=True,
            publish_succeeded=False,
            ig_post_id=None,
            permalink=None,
            failure_stage="PUBLISH",
            error_code="REMOTE_PUBLISH_PERSISTENCE_UNCERTAIN",
            publish_attempt_state=PublishAttemptState.UNKNOWN,
            publish_attempt_id="attempt-3",
        )

        res = cq.publish_next(publish_to_ig=True)
        assert res is None
        mock_mark.assert_not_called()
        mock_error.assert_called_once_with(
            1,
            "REMOTE_PUBLISH_PERSISTENCE_UNCERTAIN",
            increment_retry=False,
            preserve_attempt=True,
        )

def test_queue_generation_only_not_published():
    """생성 전용 실행을 게시 성공으로 기록하지 않음"""
    with patch("src.agents.content_queue.dequeue_next", return_value=_attested_row()), \
         patch("src.agents.content_queue._run_full_pipeline") as mock_pipeline, \
         patch("src.agents.content_queue.mark_queue_status") as mock_mark:

        mock_pipeline.return_value = PipelineResult(
            image_paths=[Path("a.png")],
            generation_succeeded=True,
            publish_requested=False,
            publish_succeeded=False,
            ig_post_id=None,
            permalink=None,
            failure_stage=None,
            error_code=None
        )

        res = cq.publish_next(publish_to_ig=False)
        assert res is not None
        mock_mark.assert_called_with(1, "ready")

def test_permalink_failure_distinct_from_publish(mock_pipeline_agents):
    """permalink 조회 실패와 media publish 실패 구분"""
    from src.pipeline import run_pipeline

    with patch("src.pipeline.ig_publisher.publish", return_value="12345"), \
         patch("src.agents.publisher.get_post_permalink", side_effect=Exception("No permalink")), \
         patch("src.db.insert_post"):

        res = run_pipeline(topic="Test", publish=True, auto=True)
        assert res.publish_succeeded is True
        assert res.ig_post_id == "12345"
        assert res.permalink is None
        assert res.publish_attempt_state == PublishAttemptState.REMOTE_ID_CONFIRMED

def test_quality_gate_disputed_claim():
    """disputed claim 차단"""
    report = FactCheckReport(
        confirmed_claim_ids=["claim-1"],
        confirmed=1,
        disputed=1,
        flagged_items=[
            FactCheckItem(
                claim_id="claim-2",
                claim="원문과 모순된 주장",
                verdict="disputed",
                note="원문은 반대 내용을 명시함",
            )
        ],
    )
    with pytest.raises(QualityGateError) as exc:
        validate_publish_quality(report, _quality_script())
    assert exc.value.error_code == "CLAIM_CONTRADICTED"

def test_quality_gate_unverifiable_claim():
    """중요 unverifiable claim 차단"""
    report = FactCheckReport(
        confirmed_claim_ids=["claim-1"],
        confirmed=1,
        unverifiable=1,
        flagged_items=[
            FactCheckItem(
                claim_id="claim-2",
                claim="근거가 부족한 주장",
                verdict="unverifiable",
                note="연결된 evidence가 없음",
            )
        ],
    )
    with pytest.raises(QualityGateError) as exc:
        validate_publish_quality(report, _quality_script())
    assert exc.value.error_code == "CLAIM_INSUFFICIENT_EVIDENCE"

def test_mock_preflight_error_classification():
    """Mock preflight 오류 분류"""
    from src.api.preflight import IGPreflightCheck, PreflightError

    def mock_client(url, params):
        return {"error": {"message": "Invalid token", "code": 190, "error_subcode": 460}}

    pf = IGPreflightCheck(http_client=mock_client)
    with pytest.raises(PreflightError) as exc:
        pf.check_token("test_token")
    assert exc.value.code == "TOKEN_EXPIRED"

def test_quality_gate_failure_blocks_publish(mock_pipeline_agents):
    """품질 게이트 실패 시 publisher 호출 0회"""
    from src.pipeline import run_pipeline
    from src.qa.deterministic_verifier import QualityGateError

    mock_pipeline_agents["cc"].side_effect = QualityGateError("QG_FAILED", "Quality gate failed")

    with patch("src.pipeline.ig_publisher.publish") as mock_publish:
        res = run_pipeline(topic="Test", publish=True, auto=True)
        assert res.publish_succeeded is False
        assert res.failure_stage == "QUALITY_GATE"
        mock_publish.assert_not_called()
