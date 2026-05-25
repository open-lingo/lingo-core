from datetime import UTC, datetime

from fastapi import APIRouter

from app.config import settings
from app.finance.schemas import FundingTransparencyResponse

router = APIRouter(tags=["finance"])


@router.get("/transparency", response_model=FundingTransparencyResponse)
async def get_funding_transparency() -> FundingTransparencyResponse:
    """
    Public funding split for the UI meter.

    MVP: ``FUNDING_AD_PERCENT`` env or default estimate.
    Future: computed from AdSense Management API + Stripe revenue snapshots.
    """
    ad = settings.funding_ad_percent
    return FundingTransparencyResponse(
        adFundedPercent=ad,
        premiumPercent=100 - ad,
        source=settings.funding_source,  # type: ignore[arg-type]
        periodLabel=settings.funding_period_label,
        updatedAt=datetime.now(UTC).isoformat(),
    )
