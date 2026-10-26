from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Any, Literal

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


class QueueEvidenceItem(BaseModel):
    """One independently attributable source retained in Queue V2."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=1000)
    url: str = Field(min_length=1, max_length=4096)
    source: str = ""
    summary: str = ""
    content: str = ""
    published_at: str = ""
    retrieved_at: str = ""
    source_material_level: Literal[
        "full_article", "partial_article", "snippet_only", "unknown"
    ] = "unknown"

    @field_validator("title", "url")
    @classmethod
    def reject_required_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("blank values are not allowed")
        return value

    @field_validator("url")
    @classmethod
    def require_http_url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("evidence url must start with http:// or https://")
        return value

    def evidence_text(self, primary_context: str = "") -> str:
        return (self.content or self.summary or primary_context or self.title).strip()

    def material_level(self) -> str:
        if self.source_material_level != "unknown":
            return self.source_material_level
        if self.content:
            return "full_article" if len(self.content) >= 1000 else "partial_article"
        if self.summary:
            return "snippet_only"
        return "unknown"


class QueueMetadataV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=2, frozen=True)
    topic: str = Field(min_length=1, max_length=500)
    source_title: str = Field(min_length=1, max_length=1000)
    source_url: str = Field(min_length=1, max_length=4096)
    context: str = Field(min_length=1)
    evidence: list[QueueEvidenceItem] = Field(min_length=1)

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

        passages: list[EvidencePassage] = [
            EvidencePassage(
                evidence_id=f"ev-{content_hash[:16]}",
                article_id=article_id,
                text=self.context,
                source_url=self.source_url,
                location="primary-context",
                content_hash=content_hash,
            )
        ]
        seen = {(self.source_url, content_hash)}
        for index, item in enumerate(self.evidence, start=1):
            text = item.evidence_text(
                self.context if item.url == self.source_url else ""
            )
            item_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
            key = (item.url, item_hash)
            if key in seen:
                continue
            seen.add(key)
            item_article_id = hashlib.sha256(
                f"{item.url}\n{item_hash}".encode("utf-8")
            ).hexdigest()
            passages.append(
                EvidencePassage(
                    evidence_id=f"ev-{index}-{item_hash[:16]}",
                    article_id=item_article_id,
                    text=text,
                    source_url=item.url,
                    location=item.title,
                    content_hash=item_hash,
                )
            )

        primary_item = next(
            (item for item in self.evidence if item.url == self.source_url),
            None,
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
            published_at=primary_item.published_at if primary_item else "",
            retrieved_at=primary_item.retrieved_at if primary_item else "",
            source_material_level=(
                primary_item.material_level() if primary_item else "partial_article"
            ),
            evidence_passages=passages,
        )
