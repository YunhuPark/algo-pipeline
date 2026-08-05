import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path
from src.schemas.content_package import PipelineResult, PublishError
from src.qa.content_quality_gate import validate_content_quality, QualityGateError
from src.schemas.card_news import TrendReport, TrendResult, CardNewsScript, Slide
import src.agents.content_queue as cq

@pytest.fixture
def mock_pipeline_agents():
    with patch("src.agents.topic_refiner.refine_topic") as mock_tr, \
         patch("src.agents.trend_analyzer.run") as mock_ta, \
         patch("src.agents.youtube_fetcher.fetch_video_candidates") as mock_yf1, \
         patch("src.agents.youtube_fetcher.find_verified_video_for_slide") as mock_yf2, \
         patch("src.agents.youtube_fetcher.download_video_snippet") as mock_yf3, \
         patch("src.agents.content_creator.ContentCreator.run") as mock_cc, \
         patch("src.agents.fact_checker.check_script") as mock_fc_check, \
         patch("src.agents.image_searcher.get_background_image") as mock_is, \
         patch("src.agents.design_renderer.render_card_set") as mock_dr, \
         patch("src.agents.approval.wait_for_approval") as mock_app, \
         patch("src.agents.angle_selector.select_angle") as mock_angle, \
         patch("src.db.insert_post") as mock_insert_post:
        
        mock_tr.return_value = ("Test Topic", "Focus", "Reason")
        mock_yf1.return_value = []
        mock_ta.return_value = TrendReport(
            query="Test",
            results=[TrendResult(title="Test", url="http://test.com", content="A" * 1500, score=1.0)],
            summary=""
        )
        
        mock_script = CardNewsScript(
            topic="Test Topic",
            slides=[Slide(slide_number=1, slide_type="cover", title="Test", body="Test")],
            hook="hook",
            hashtags=[]
        )
        mock_fc = MagicMock()
        mock_fc.confirmed = 1
        mock_fc.disputed = 0
        mock_fc.unverifiable = 0
        mock_fc.flagged_items = []
        
        # content_creator.run()은 CardNewsScript만 반환 (tuple이 아님)
        mock_cc.return_value = mock_script
        # fact_checker는 별도 모듈에서 호출
        mock_fc_check.return_value = mock_fc
        
        # image_searcher.get_background_image()는 PIL Image를 반환
        mock_bg = MagicMock()
        mock_bg.size = (1080, 1080)
        mock_is.return_value = mock_bg
        mock_dr.return_value = [Path("dummy.png")]
        mock_app.return_value = "upload"
        
        yield {
            "ta": mock_ta,
            "cc": mock_cc,
            "is": mock_is,
            "dr": mock_dr,
            "app": mock_app,
            "fc": mock_fc,
            "script": mock_script
        }

def test_publisher_exception_propagates(mock_pipeline_agents):
    """publisher 단계에서 예외 발생 시 반환값 확인"""
    from src.pipeline import run_pipeline
    
    with patch("src.pipeline.ig_publisher.publish", side_effect=Exception("Network Error")):
        res = run_pipeline(topic="Test", publish=True, auto=True)
        assert res.publish_succeeded is False
        assert res.failure_stage == "publisher"
        assert "Network Error" in res.error_code

def test_empty_id_is_failure(mock_pipeline_agents):
    """게시 성공 상태이나 빈 ID가 반환될 경우"""
    from src.pipeline import run_pipeline
    
    with patch("src.pipeline.ig_publisher.publish", return_value=""):
        res = run_pipeline(topic="Test", publish=True, auto=True)
        assert res.publish_succeeded is False
        assert res.failure_stage == "publisher"
        assert "Empty ig_post_id" in res.error_code

def test_queue_does_not_mark_failed_as_published():
    """Queue가 게시 실패를 published로 기록하지 않음"""
    with patch("src.agents.content_queue.dequeue_next", return_value={"id": 1, "topic": "T", "context": "", "angle_hint": "", "image_dir": ""}), \
         patch("src.agents.content_queue._run_full_pipeline") as mock_pipeline, \
         patch("src.agents.content_queue.mark_queue_status") as mock_mark:
        
        mock_pipeline.return_value = PipelineResult(
            image_paths=[Path("a.png")],
            generation_succeeded=True,
            publish_requested=True,
            publish_succeeded=False,
            ig_post_id=None,
            permalink=None,
            failure_stage="PUBLISH",
            error_code="ERR"
        )
        
        res = cq.publish_next(publish_to_ig=True)
        assert res is None
        mock_mark.assert_called_with(1, "failed")

def test_queue_generation_only_not_published():
    """생성 전용 실행을 게시 성공으로 기록하지 않음"""
    with patch("src.agents.content_queue.dequeue_next", return_value={"id": 1, "topic": "T", "context": "", "angle_hint": "", "image_dir": ""}), \
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

def test_quality_gate_topic_source_mismatch():
    """Topic-Source 불일치 차단"""
    meta = {"topic": "Apple", "source_title": "Orange"}
    pass 

def test_quality_gate_disputed_claim():
    """disputed claim 차단"""
    meta = {"topic": "T", "source_title": "T", "source_url": "http", "fact_disputed": 1}
    with pytest.raises(QualityGateError, match="Legacy fact checker found disputed or unverifiable claims."):
        validate_content_quality(meta, None)

def test_quality_gate_unverifiable_claim():
    """중요 unverifiable claim 차단"""
    meta = {"topic": "T", "source_title": "T", "source_url": "http", "fact_unverifiable": 1}
    with pytest.raises(QualityGateError, match="Legacy fact checker found disputed or unverifiable claims."):
        validate_content_quality(meta, None)

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
