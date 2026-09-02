from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FactCheckItem(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    claim_id: str = Field(..., min_length=1)
    claim: str = Field(..., min_length=1)
    verdict: Literal["disputed", "unverifiable"]
    note: str = Field(..., min_length=1)


class FactCheckReport(BaseModel):
    """Auditable result emitted only after both V2 claim gates pass."""

    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal["2.0"] = "2.0"
    confirmed_claim_ids: list[str] = Field(default_factory=list)
    confirmed: int = 0
    disputed: int = 0
    unverifiable: int = 0
    flagged_items: list[FactCheckItem] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_counts(self) -> "FactCheckReport":
        if len(self.confirmed_claim_ids) != len(set(self.confirmed_claim_ids)):
            raise ValueError("confirmed_claim_ids must be unique")
        if self.confirmed != len(self.confirmed_claim_ids):
            raise ValueError("confirmed must equal the number of confirmed_claim_ids")
        if min(self.confirmed, self.disputed, self.unverifiable) < 0:
            raise ValueError("fact-check counts cannot be negative")

        flagged_ids = [item.claim_id for item in self.flagged_items]
        if len(flagged_ids) != len(set(flagged_ids)):
            raise ValueError("flagged claim IDs must be unique")
        if set(self.confirmed_claim_ids) & set(flagged_ids):
            raise ValueError("a claim cannot be both confirmed and flagged")

        disputed_items = sum(
            item.verdict == "disputed" for item in self.flagged_items
        )
        unverifiable_items = sum(
            item.verdict == "unverifiable" for item in self.flagged_items
        )
        if self.disputed != disputed_items:
            raise ValueError("disputed must equal the number of disputed flagged_items")
        if self.unverifiable != unverifiable_items:
            raise ValueError(
                "unverifiable must equal the number of unverifiable flagged_items"
            )
        return self
