import json
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

from src.schemas.card_news import SourceLineage, CardNewsScript, Slide, Claim
from src.schemas.content_package import PipelineResult

@pytest.fixture
def mock_db(tmp_path):
    db_path = tmp_path / "algo.db"
    with patch("src.db.DB_PATH", db_path, create=True):
        from src.db import init_db
        init_db()
        yield db_path

@pytest.fixture
def mock_tracking_db(tmp_path):
    db_path = tmp_path / "tracking.db"
    with patch("src.analytics.db_experiments.TRACKING_DB_PATH", db_path, create=True):
        from src.analytics.db_experiments import init_tracking_db
        init_tracking_db()
        yield db_path

def test_run_daily_empty_queue_news_success(mock_db, tmp_path):
    """큐가 비었을 때 실제 선택 기사의 topic/source가 함께 전달됨 & 뉴스가 없으면 파이프라인이 실행되지 않음"""
    with patch("scripts.run_daily._queue_pending", return_value=False), \
         patch("scripts.run_daily._notify"), \
         patch("src.agents.news_collector.collect_and_select") as mock_collect, \
         patch("src.pipeline.run_pipeline") as mock_pipeline:
         
        from src.agents.news_collector import NewsSelection, NewsItem
        mock_collect.return_value = NewsSelection(
            topic="Test Topic",
            reason="Test",
            context="Test Context",
            source_items=[NewsItem(title="Test Source Title", url="http://test.com", source="Test Source", summary="Test Summary")]
        )
        mock_pipeline.return_value = PipelineResult(
            image_paths=[], generation_succeeded=True, publish_requested=True,
            publish_succeeded=True, ig_post_id="123", permalink="", failure_stage=None, error_code=None
        )
        
        from scripts.run_daily import main
        with patch("sys.exit") as mock_exit:
            main()
            
        mock_exit.assert_called_with(0)
        mock_pipeline.assert_called_once()
        kwargs = mock_pipeline.call_args.kwargs
        assert kwargs["topic"] == "Test Topic"
        assert kwargs["source_lineage"].source_title == "Test Source Title"
        assert kwargs["source_lineage"].source_url == "http://test.com"
        assert kwargs["source_lineage"].context == "Test Context"

def test_run_daily_news_collection_failure(mock_db):
    """뉴스 수집 실패 시 가상 topic이 생성되지 않음 (fail-closed)"""
    with patch("scripts.run_daily._queue_pending", return_value=False), \
         patch("scripts.run_daily._notify"), \
         patch("src.agents.news_collector.collect_and_select", side_effect=Exception("API Error")), \
         patch("src.pipeline.run_pipeline") as mock_pipeline:
         
        from scripts.run_daily import main
        with patch("sys.exit") as mock_exit:
            main()
            
        mock_exit.assert_called_with(1)
        mock_pipeline.assert_not_called()

def test_pipeline_metadata_and_folder_name(mock_db, tmp_path):
    """입력 topic과 script.topic이 다를 때 metadata와 DB에는 script.topic 저장, output 폴더명과 일치"""
    from src.pipeline import run_pipeline
    with patch("src.agents.content_creator.ContentCreator.run") as mock_cc_run, \
         patch("src.agents.publisher.publish", return_value="test_id_123"), \
         patch("src.agents.design_renderer.render_card_set") as mock_render, \
         patch("src.qa.content_quality_gate.validate_content_quality") as mock_qg, \
         patch("src.agents.youtube_fetcher.fetch_video_candidates", return_value=[]), \
         patch("src.config.OUTPUT_DIR", tmp_path):
         
        # mock content creator to return a different topic
        script = CardNewsScript(
            topic="Refined Script Topic",
            hook="Test Hook",
            slides=[Slide(slide_number=1, slide_type="cover", title="T", body="B", emoji="", accent="")],
            hashtags=["#test"]
        )
        mock_cc_run.return_value = script
        
        # mock design renderer to save files in a specific dir based on script.topic
        out_dir = tmp_path / "20260728_1200_Refined_Script_Topic"
        out_dir.mkdir(parents=True)
        mock_render.return_value = [out_dir / "card_01_cover.png"]
        
        lineage = MagicMock(spec=SourceLineage)
        lineage.topic = "Original Input Topic"
        lineage.source_title = "Test Source Title"
        lineage.source_url = "http://test.com"
        lineage.context = "Test Context"
        lineage.article_id = "art_123"
        lineage.fact_checked = True
        lineage.evidence_passages = []
        
        res = run_pipeline(
            topic="Original Input Topic",
            publish=True,
            source_lineage=lineage,
            auto=True
        )
        
        assert res is not None
        assert res.publish_succeeded is True
        
        # Check DB
        from src.db import get_posts
        posts = get_posts()
        assert len(posts) == 1
        assert posts[0]["topic"] == "Refined Script Topic"  # Must match script.topic
        assert posts[0]["post_id"] == "test_id_123"
        
        # Check meta.json
        meta_file = out_dir / "meta.json"
        assert meta_file.exists()
        meta_data = json.loads(meta_file.read_text(encoding="utf-8"))
        assert meta_data["topic"] == "Refined Script Topic"
        assert meta_data["source_title"] == "Test Source Title"
        assert meta_data["source_url"] == "http://test.com"
        assert meta_data["article_id"] == "art_123"

def test_pipeline_quality_gate_failure(mock_db, tmp_path):
    """기존 Quality Gate 실패 시 publisher 미호출 확인"""
    from src.pipeline import run_pipeline
    with patch("src.agents.content_creator.run") as mock_cc_run, \
         patch("src.agents.publisher.publish") as mock_publish, \
         patch("src.agents.design_renderer.render_card_set") as mock_render, \
         patch("src.qa.content_quality_gate.validate_content_quality", side_effect=Exception("QG Error")), \
         patch("src.agents.youtube_fetcher.fetch_video_candidates", return_value=[]), \
         patch("src.config.OUTPUT_DIR", tmp_path):
         
        script = CardNewsScript(
            topic="QG Fail Topic", hook="Hook",
            slides=[Slide(slide_number=1, slide_type="cover", title="T", body="B", emoji="", accent="")],
            hashtags=["#test"]
        )
        mock_cc_run.return_value = script
        
        out_dir = tmp_path / "QG_FAIL_DIR"
        out_dir.mkdir(parents=True)
        mock_render.return_value = [out_dir / "card_01_cover.png"]
        
        lineage = SourceLineage(
            topic="Input", source_title="S", source_url="U", context="C"
        )
        
        res = run_pipeline(
            topic="Input",
            publish=True,
            source_lineage=lineage,
            auto=True
        )
        
        # Publisher should NOT be called
        mock_publish.assert_not_called()
        assert res.publish_succeeded is False
        assert res.failure_stage == "QUALITY_GATE"
