from typing import Literal

from pydantic import BaseModel, Field


class FundingTransparencyResponse(BaseModel):
    """Public sustainability snapshot for the funding meter (no PII)."""

    ad_funded_percent: int = Field(..., ge=0, le=100, alias="adFundedPercent")
    premium_percent: int = Field(..., ge=0, le=100, alias="premiumPercent")
    source: Literal["manual", "estimated", "live"] = "estimated"
    period_label: str = Field("Last 30 days", alias="periodLabel")
    updated_at: str | None = Field(None, alias="updatedAt")

    model_config = {"populate_by_name": True}
