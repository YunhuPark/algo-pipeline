from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CollectionMethod(str, Enum):
    NEWS_COLLECTOR = "NEWS_COLLECTOR"
    MANUAL_VERIFIED = "MANUAL_VERIFIED"
    LEGACY_UNVERIFIED = "LEGACY_UNVERIFIED"
    TEST_FIXTURE = "TEST_FIXTURE"
    SYNTHETIC = "SYNTHETIC"


class PublishAttemptState(str, Enum):
    NOT_ATTEMPTED = "NOT_ATTEMPTED"
    STARTED = "STARTED"
    REMOTE_ID_CONFIRMED = "REMOTE_ID_CONFIRMED"
    UNKNOWN = "UNKNOWN"


class RetryDisposition(str, Enum):
    RETRYABLE = "RETRYABLE"
    PERMANENT = "PERMANENT"
    UNCERTAIN = "UNCERTAIN"


class QueueMetadataV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=2, frozen=True)
    topic: str = Field(min_length=1, max_length=500)
    source_title: str = Field(min_length=1, max_length=1000)
    source_url: str = Field(min_length=1, max_length=4096)
    context: str = Field(min_length=1)
    evidence: list[dict[str, Any]] = Field(min_length=1)

    @field_validator("topic", "source_title", "source_url", "context")
    @classmethod
    def reject_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("blank values are not allowed")
        return value

    def canonical_json(self) -> str:
        return json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    def lineage_hash(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    def to_source_lineage(self, collection_method: CollectionMethod):
        """Build the verified pipeline lineage from the attested queue payload."""
        from .card_news import EvidencePassage, SourceLineage

        content_hash = hashlib.sha256(self.context.encode("utf-8")).hexdigest()
        article_id = hashlib.sha256(
            f"{self.source_url}\n{content_hash}".encode("utf-8")
        ).hexdigest()
        evidence = EvidencePassage(
            evidence_id=f"ev-{content_hash[:16]}",
            article_id=article_id,
            text=self.context,
            source_url=self.source_url,
            content_hash=content_hash,
        )
        return SourceLineage(
            schema_version="2.0",
            topic=self.topic,
            source_title=self.source_title,
            source_url=self.source_url,
            context=self.context,
            article_id=article_id,
            content_hash=content_hash,
            collection_method=collection_method.value,
            source_material_level="partial_article",
            evidence_passages=[evidence],
        )
