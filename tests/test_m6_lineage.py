import json
from types import SimpleNamespace
from unittest.mock import patch, MagicMock
import pytest

from src.schemas.card_news import EvidencePassage, SourceLineage, CardNewsScript, Slide
from src.schemas.fact_check import FactCheckReport

@pytest.fixture
def mock_db(tmp_path, monkeypatch):
    db_path = tmp_path / "algo.db"
    monkeypatch.setenv("ALGO_ENV", "test")
    monkeypatch.setenv("ALGO_DB_PATH", str(db_path))
    from src.db import init_db
    init_db(db_path)
    yield db_path

@pytest.fixture
def mock_tracking_db(tmp_path):
    db_path = tmp_path / "tracking.db"
    with patch("src.analytics.db_experiments.TRACKING_DB_PATH", db_path, create=True):
        from src.analytics.db_experiments import init_tracking_db
        init_tracking_db()
        yield db_path

def test_run_daily_empty_queue_routes_through_queue_cli(mock_db, tmp_path):
    """빈 큐 자동화는 Queue V2 등록 후 durable publish CLI만 호출한다."""
    lock_file = tmp_path / "logs" / "pipeline.lock"
    with patch("scripts.run_daily._queue_pending", return_value=False), \
         patch("scripts.run_daily._try_acquire_lock", return_value=True), \
         patch("scripts.run_daily.LOCK_FILE", lock_file), \
         patch("scripts.run_daily._notify"), \
         patch(
             "scripts.run_daily.resolve_automation_mode",
             return_value=SimpleNamespace(live_publish=True),
         ), \
         patch("src.queue_runtime.prepare_queue_runtime"), \
         patch("scripts.run_daily.subprocess.run", side_effect=[
             SimpleNamespace(returncode=0),
             SimpleNamespace(returncode=0),
         ]) as run:
        from scripts.run_daily import main
        with patch("sys.exit") as mock_exit:
            main()

        mock_exit.assert_called_with(0)
        assert run.call_args_list[0].args[0][-2:] == ["--queue", "1"]
        assert run.call_args_list[1].args[0][-2:] == ["--queue-publish", "--publish"]


def test_run_daily_safe_default_preserves_pending_queue(mock_db, tmp_path):
    """자동 게시 비활성 모드는 큐를 생성하되 publisher CLI를 호출하지 않는다."""
    lock_file = tmp_path / "logs" / "pipeline.lock"
    with patch("scripts.run_daily._queue_pending", return_value=False), \
         patch("scripts.run_daily._try_acquire_lock", return_value=True), \
         patch("scripts.run_daily.LOCK_FILE", lock_file), \
         patch("scripts.run_daily._notify"), \
         patch(
             "scripts.run_daily.resolve_automation_mode",
             return_value=SimpleNamespace(live_publish=False),
         ), \
         patch("src.queue_runtime.prepare_queue_runtime"), \
         patch(
             "scripts.run_daily.subprocess.run",
             return_value=SimpleNamespace(returncode=0),
         ) as run:
        from scripts.run_daily import main
        with patch("sys.exit") as mock_exit:
            main()

        mock_exit.assert_called_with(0)
        run.assert_called_once()
        assert run.call_args.args[0][-2:] == ["--queue", "1"]

def test_run_daily_queue_ingestion_failure_is_fail_closed(mock_db, tmp_path):
    """Queue V2 등록 실패 시 publisher CLI를 호출하지 않는다."""
    lock_file = tmp_path / "logs" / "pipeline.lock"
    with patch("scripts.run_daily._queue_pending", return_value=False), \
         patch("scripts.run_daily._try_acquire_lock", return_value=True), \
         patch("scripts.run_daily.LOCK_FILE", lock_file), \
         patch("scripts.run_daily._notify"), \
         patch("src.queue_runtime.prepare_queue_runtime"), \
         patch("scripts.run_daily.subprocess.run", return_value=SimpleNamespace(returncode=1)) as run:
        from scripts.run_daily import main
        with pytest.raises(RuntimeError, match="Queue V2 뉴스 등록 실패"):
            main()

        run.assert_called_once()


def _lineage(topic="Original Input Topic"):
    evidence = EvidencePassage(
        evidence_id="ev-1",
        article_id="art-123",
        text="Test Context",
        source_url="http://test.com",
        content_hash="content-hash",
    )
    return SourceLineage(
        schema_version="2.0",
        topic=topic,
        source_title="Test Source Title",
        source_url="http://test.com",
        context="Test Context",
        article_id="art-123",
        content_hash="content-hash",
        collection_method="NEWS_COLLECTOR",
        source_material_level="partial_article",
        evidence_passages=[evidence],
    )


@pytest.fixture
def pipeline_seams(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    script = CardNewsScript(
        topic="Refined Script Topic",
        hook="Test Hook",
        slides=[
            Slide(slide_number=1, slide_type="cover", title="T", body="B"),
            Slide(slide_number=2, slide_type="content", title="Fact", body="Verified fact"),
            Slide(slide_number=3, slide_type="cta", title="Source", body="Read source"),
        ],
        hashtags=["#test"],
    )
    out_dir = tmp_path / "20260728_1200_Refined_Script_Topic"
    out_dir.mkdir(parents=True)
    background = MagicMock()
    background.size = (1080, 1080)
    fact_report = FactCheckReport(
        confirmed_claim_ids=["claim-1"],
        confirmed=1,
        disputed=0,
        unverifiable=0,
        flagged_items=[],
    )
    with patch("src.pipeline.content_creator.ContentCreator") as creator, \
         patch("src.pipeline.image_searcher.get_background_image", return_value=background), \
         patch("src.pipeline.design_renderer.render_card_set", return_value=[out_dir / "card_01_cover.png"]), \
         patch("src.agents.fact_checker.check_script", return_value=fact_report), \
         patch("src.agents.youtube_fetcher.fetch_video_candidates", return_value=[]), \
         patch("src.agents.publisher.get_post_permalink", return_value="https://instagram.test/p/123"), \
         patch("subprocess.run"):
        creator.return_value.run.return_value = script
        creator.return_value.last_fact_check_report = fact_report
        yield script, out_dir

def test_pipeline_metadata_and_folder_name(mock_db, pipeline_seams):
    """입력 topic과 script.topic이 다를 때 metadata와 DB에는 script.topic 저장, output 폴더명과 일치"""
    from src.pipeline import run_pipeline
    _, out_dir = pipeline_seams
    with patch("src.pipeline.ig_publisher.publish", return_value="test_id_123"):
        res = run_pipeline(
            topic="Original Input Topic",
            publish=True,
            source_lineage=_lineage(),
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
        assert meta_data["article_id"] == "art-123"

def test_pipeline_quality_gate_failure(mock_db, pipeline_seams):
    """V2 publish Quality Gate 실패 시 publisher 미호출 확인"""
    from src.pipeline import run_pipeline
    from src.qa.deterministic_verifier import QualityGateError
    with patch("src.pipeline.ig_publisher.publish") as mock_publish, \
         patch(
             "src.qa.publish_quality_gate.validate_publish_quality",
             side_effect=QualityGateError("QG_ERROR", "QG Error"),
         ):
        res = run_pipeline(
            topic="Input",
            publish=True,
            source_lineage=_lineage("Input"),
            auto=True
        )

        # Publisher should NOT be called
        mock_publish.assert_not_called()
        assert res.failure_stage == "QUALITY_GATE"
        assert res.error_code == "QG_ERROR"
        assert res.publish_succeeded is False
        assert res.failure_stage == "QUALITY_GATE"
