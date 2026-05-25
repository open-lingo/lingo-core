# XP curve — design (2026-05-25)

## Today

`lingo/src/features/progress/leveling.ts` exposes `xpForLevel(n) = 100 * n`,
a flat linear curve. Each level is 100 XP cheaper than every prior MMO would
suggest, and there's no compression to stop a power-user from racing past
level 50 in a weekend.

## Proposed curve

Polynomial soft curve, capped at level 100:

```
xpForLevel(n) = 50 * n * (n + 9)
```

Hand-table for sanity:

| Level | XP required | Cumulative |
| ----- | ----------- | ---------- |
| 1     | 500         | 500        |
| 5     | 3,500       | 11,500     |
| 10    | 9,500       | 47,500     |
| 25    | 42,500      | 471,250    |
| 50    | 147,500     | 2,562,500  |
| 100   | 545,000     | 18,750,000 |

The linear case (`100*n`) is preserved at very early levels (in spirit —
500 vs 100 keeps the same "level often" cadence given that real sessions
yield ~50–80 XP), then expands. The cap stops infinite grinding once
`xpForLevel` would exceed a year of daily lessons.

## XP sources + caps

Sources and per-day caps. Caps reset at the same 00:00 UTC boundary as the
weekly leaderboard.

| Source                         | XP per event | Daily cap |
| ------------------------------ | ------------ | --------- |
| Lesson completion (passed)     | 10–30        | 200       |
| Lesson completion (perfect)    | +10 bonus    | n/a       |
| Flashcard review (graded card) | 1            | 50        |
| Daily quest claim              | varies       | 60        |
| Weekly quest claim             | varies       | n/a       |
| Streak milestone (7d/30d/100d) | 50/200/1000  | n/a       |
| League podium (#1/#2/#3)       | 200/100/50   | once/week |

The caps prevent a one-day binge from inflating someone's league position
beyond what their sustainable practice rate would yield.

## Persistence

The backend already stores `users.xp` (INTEGER) — no schema change.
"Level" is derived on read via `xpForLevel`, never persisted. XP awarded
flows through:

- Lesson completion → `progress` writes the attempt + bumps day rollup +
  patches `users.xp`. (Already wired today.)
- Quest claim → `quests.claim` flips status; the router patches
  `users.xp` and `users.lingots`. (Wired in this change.)
- Streak / league rewards → a new internal helper
  `app/shared/xp.py:grant_xp(user_id, source, amount)` that enforces the
  per-source cap by reading the day rollup before writing.

## Display

- `ProfileCard`: current level + XP bar to next level.
- `QuestsPanel` header: today's XP earned vs daily cap (visual cap hint).
- `SocialHeader`: weekly XP + league chip — already there.
- League cycle close: animated XP-to-level conversion if a level-up was
  unlocked during the closing snapshot.

## Migration

Existing user XP values stay as-is — the new curve simply spreads them
across fewer levels. A one-time backfill is optional ("re-grant the
level-up animation for each new level crossed") but not required for the
math to be correct.
