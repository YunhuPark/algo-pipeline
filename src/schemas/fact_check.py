from typing import Literal

from pydantic import BaseModel, Field, model_validator


class FactCheckItem(BaseModel):
    claim_id: str = Field(..., min_length=1)
    claim: str = Field(..., min_length=1)
    verdict: Literal["disputed", "unverifiable"]
    note: str = Field(..., min_length=1)


class FactCheckReport(BaseModel):
    """Auditable result emitted only after both V2 claim gates pass."""

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
        return self
