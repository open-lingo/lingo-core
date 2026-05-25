# Leagues — design (2026-05-25)

## Tiers

Four leagues, three tiers each, plus an open-ended Obsidian band:

- Bronze (I–III)
- Silver (I–III)
- Gold (I–III)
- Diamond (I–III)
- Obsidian (no tiers, capped cohort size)

Initial seeding maps the current weekly-XP brackets in `_league_for_weekly_xp`
(0–99 → Bronze, 100–499 → Silver, 500–1499 → Gold, 1500–4999 → Diamond,
5000+ → Obsidian) onto the appropriate tier inside each league.

## Cohort + cycle

- Cohort size: 30 learners. Smaller than Duo's 30–50 by design — keeps the
  podium feel even at low DAU.
- Cycle: weekly. Cohorts close at 00:00 UTC every Monday. We reuse the
  weekly XP buckets already returned by `_xp_in_window(days=7)`.
- Promotion: top 7 of 30 advance one tier.
- Demotion: bottom 5 of 30 drop one tier (Bronze I floor — no demotion
  below it).
- Sandbagging: the remaining 18 stay in place. A learner who finishes 0 XP
  is auto-demoted regardless of cohort position to stop "smurfing".

## How it relates to the existing leaderboard

`social_leaderboard` already carries `bucket` strings like
`"weekly:<lang>"` — leagues add a parallel `bucket = "league:<cohort_id>"`
row per user per week. The cohort id is stable for the duration of the week
and stored on the user row (`league_cohort_id`) for join performance.

A cron-style server job (Lambda EventBridge in prod, `make leagues:tick` in
dev) runs at cycle close: snapshot ranks, mutate `users.league_tier` +
`users.league_cohort_id`, write a `leagues_history` row for receipts.

## Anti-grind

- Per-quest + per-source XP caps (see `xp-curve-design-2026-05-25.md`)
  feed back here — league XP === XP-curve XP.
- A learner whose 7-day XP is more than 3× the cohort median triggers a
  soft flag for moderator review; no auto-action yet.

## UI surfaces

- `LeagueSpotlightCard` in social — already partly there. Add the cohort
  rank, tier badge, time-until-tick.
- Profile pages: tier badge next to the avatar (cosmetic-system slot).
- End-of-cycle modal: "you finished #N in <league name>" with promotion/
  demotion animation.

## Server-side surface

- `users.league_tier` + `users.league_cohort_id` columns.
- `leagues_cohorts` table: `id`, `tier`, `created_at`, `closes_at`,
  `member_count`.
- `leagues_history` row per (user, cycle) for personal history view.
- `GET /api/core/v1/social/leagues/me` — current tier, cohort id, rank.
- `GET /api/core/v1/social/leagues/cohort` — cohort leaderboard, reuses
  `_build_leaderboard` with `cohort_ids` populated from the join.
- Weekly tick job that writes the snapshot + reassigns cohorts.

## Out of scope (v1)

- Cross-language cohorts. v1 keeps cohorts language-agnostic; weekly XP
  is summed across languages.
- League-locked cosmetics. Those live in the cosmetics design doc.
