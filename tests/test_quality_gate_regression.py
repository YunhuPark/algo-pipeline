import pytest
from src.qa.deterministic_verifier import DeterministicVerifier, QualityGateError
from src.schemas.card_news import SourceLineage, EvidencePassage, Claim, NormalizedNumber
from src.pipeline import run_pipeline, _run_once
from unittest.mock import patch, MagicMock

@pytest.fixture
def lineage_no_5():
    return SourceLineage(
        schema_version="2.0",
        topic="OpenAI Agents",
        source_title="OpenAI Agents",
        source_url="http://test.com",
        context="Agents ran amok.",
        article_id="art_1",
        content_hash="hash",
        evidence_passages=[
            EvidencePassage(evidence_id="ev_1", article_id="art_1", source_url="http://test.com", text="Agents ran amok. They are dangerous. We need to be careful. No numbers here.", content_hash="hash")
        ]
    )

def test_quality_gate_number_unsupported_hallucination(lineage_no_5):
    verifier = DeterministicVerifier()
    
    # 5가지 특징 hallucination in Claim
    claim = Claim(
        claim_id="c1",
        claim_text="OpenAI Agents 5가지 특징",
        claim_type="factual",
        numbers=[NormalizedNumber(raw_text="5가지", normalized_value=5.0, unit="가지", qualifier="", subject="")],
        evidence_ids=["ev_1"],
        source_url="http://test.com"
    )
    
    with pytest.raises(QualityGateError) as exc_info:
        verifier.verify_claims([claim], lineage_no_5)
    
    assert "not supported by evidence" in str(exc_info.value)
    assert "5가지" in str(exc_info.value)

@patch("src.pipeline.content_creator.ContentCreator")
@patch("src.agents.design_renderer.render_card_set")
@patch("src.agents.publisher.publish")
@patch("src.agents.angle_selector.select_angle")
@patch("src.agents.youtube_fetcher.fetch_video_candidates", return_value=[])
def test_pipeline_aborts_on_hallucinated_number(
    mock_video, mock_angle, mock_publish, mock_render, mock_creator, lineage_no_5
):
    # If ContentCreator raises QualityGateError (which it does via DeterministicVerifier)
    # The pipeline should fail closed
    mock_creator.return_value.run.side_effect = QualityGateError(
        "NUMBER_UNSUPPORTED", "Number '5만' not supported by evidence."
    )
    mock_angle.return_value = None
    
    res = run_pipeline("OpenAI Agents", publish=True, source_lineage=lineage_no_5, auto=True)
    
    assert res.generation_succeeded is False
    assert res.failure_stage == "QUALITY_GATE"
    
    mock_render.assert_not_called()
    mock_publish.assert_not_called()
