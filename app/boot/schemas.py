"""Response schema for the batched boot read."""

from pydantic import BaseModel

from app.progress.schemas import ProgressSummary, TouchResponse, UnlockMapResponse
from app.quests.schemas import QuestListResponse
from app.srs.schemas import SRSStateResponse
from app.users.schemas import SubscriptionItem, UserResponse, UserSettings


class BootResponse(BaseModel):
    """Everything the client's boot sequence used to fetch as 6–8 parallel
    requests, in one payload.

    ``quests`` / ``subscriptions`` are optional: their repos are absent in
    some environments (``require_repo`` 503s), and a missing side-widget
    must not fail the whole boot read — the client falls through to the
    individual endpoint for a ``null`` section.
    """

    user: UserResponse
    settings: UserSettings
    progress: ProgressSummary
    unlocks: UnlockMapResponse
    touch: TouchResponse
    srs: SRSStateResponse
    quests: QuestListResponse | None = None
    subscriptions: list[SubscriptionItem] | None = None
