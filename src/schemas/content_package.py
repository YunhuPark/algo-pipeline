from pydantic import BaseModel, Field
from typing import Literal, Optional, List, Any
from .card_news import CardNewsScript, TrendReport
from dataclasses import dataclass
from pathlib import Path

class PublishError(Exception):
    pass

@dataclass(frozen=True)
class PipelineResult:
    image_paths: list[Path]
    generation_succeeded: bool
    publish_requested: bool
    publish_succeeded: bool
    ig_post_id: str | None
    permalink: str | None
    failure_stage: str | None
    error_code: str | None

PipelineStatus = Literal[
    "CANDIDATE",
    "RESEARCHED",
    "CLAIMS_EXTRACTED",
    "FACT_VERIFIED",
    "STORY_PLANNED",
    "DRAFTED",
    "CRITIC_REVIEWED",
    "RENDERED",
    "VISUAL_QA_PASSED",
    "CAPTION_QA_PASSED",
    "APPROVED",
    "PUBLISHABLE",
    "NEEDS_REVIEW",
    "REJECTED",
    "SKIP_PUBLICATION"
]

class GenerationStrategy(BaseModel):
    topic_strategy: str = "default"
    hook_strategy: str = "default"
    story_structure: str = "default"
    template_id: str = "default"
    slide_count: int = 6
    cta_strategy: str = "default"
    policy_version: str = "1.0.0"

class ContentPackage(BaseModel):
    post_id: str
    topic: str
    event_id: str = ""
    status: PipelineStatus = "CANDIDATE"
    strategy: Optional[GenerationStrategy] = None
    
    # 1. Research
    trend_report: Optional[TrendReport] = None
    
    # 2. Fact Check
    extracted_claims: List[Any] = Field(default_factory=list)
    fact_check_report: Optional[Any] = None  # FactCheckReport
    
    # 3. Draft
    draft_script: Optional[CardNewsScript] = None
    
    # 4. Critic
    critic_report: Optional[Any] = None
    
    # 5. Render
    rendered_image_paths: List[str] = Field(default_factory=list)
    
    # 6. QA
    visual_qa: Optional[Any] = None
    caption_qa: Optional[Any] = None
    
    # 7. Final Scoring
    quality_score: int = 0
    quality_policy_version: str = "1.0.0"
    approval_status: str = "PENDING"
