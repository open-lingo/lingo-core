"""League tier derivation from a user's cumulative XP.

This is the server-side mirror of the FE ladder defined in
``lingo/src/features/social/data/leagueTiers.ts``. Keep the two in sync —
the FE uses ``LeagueInfo.tierIndex`` to highlight the matching row in the
leagues modal, so the indexes and names must line up.

The thresholds here describe lifetime XP, not weekly XP, so a profile
view can pick a tier from the user row alone (no leaderboard read). The
FE thresholds in ``leagueTiers.ts`` are weekly-XP soft targets used for
the modal copy, not for tier assignment.
"""

from app.social.schemas import LeagueBadge

# Order matches the FE ladder: index 0 = lowest, index N-1 = highest.
# Threshold is the lifetime XP needed to *reach* this tier; the user's
# tier is the highest threshold they've cleared.
_LEAGUE_LADDER: tuple[tuple[str, str, int], ...] = (
    ("Bronze League", "🥉", 0),
    ("Silver League", "🥈", 200),
    ("Gold League", "🥇", 750),
    ("Emerald League", "💚", 2_000),
    ("Sapphire League", "💎", 5_000),
    ("Ruby League", "❤️", 10_000),
    ("Amethyst League", "🟣", 18_000),
    ("Pearl League", "🤍", 30_000),
    ("Obsidian League", "⚫", 50_000),
    ("Diamond League", "💠", 80_000),
)


def league_for_xp(xp: int) -> LeagueBadge | None:
    """Return the league badge for the user's lifetime XP, or None when
    they haven't earned anything yet.

    The user's tier is the highest ladder entry they've cleared; XP=0
    returns None (no badge until their first lesson lands).
    """
    if xp <= 0:
        return None
    name, emoji, _threshold = _LEAGUE_LADDER[0]
    chosen_index = 0
    for idx, (n, e, t) in enumerate(_LEAGUE_LADDER):
        if xp >= t:
            name, emoji, chosen_index = n, e, idx
        else:
            break
    return LeagueBadge(name=name, tier_index=chosen_index, emoji=emoji)
