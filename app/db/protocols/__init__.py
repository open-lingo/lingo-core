"""Repository protocol contracts — one file per domain.

Import from the submodule directly for clarity, or use this package-level
re-export for backward compatibility:

    from app.db.protocols import UserRepository, SRSRepository
    from app.db.protocols.srs import SRSRepository        # explicit
"""

from app.db.protocols.community import CommunityRepository
from app.db.protocols.deck import DeckRepository
from app.db.protocols.srs import SRSRepository
from app.db.protocols.story import StoryRepository
from app.db.protocols.subscription import SubscriptionRepository
from app.db.protocols.user import UserRepository

__all__ = [
    "UserRepository",
    "SRSRepository",
    "DeckRepository",
    "StoryRepository",
    "SubscriptionRepository",
    "CommunityRepository",
]
