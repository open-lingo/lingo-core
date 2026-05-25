# Cosmetics — design (2026-05-25)

## What "cosmetic" means here

A visual flair attached to a user's identity that does not affect gameplay.
The three slot types we plan to ship:

1. **Avatar frames** — the ring around the profile picture. Already
   prototyped in `UserAvatar.tsx` via the `AvatarFrame` union (`none`,
   `solid`, `gradient`, `animated`). Mock data ships a hardcoded frame on
   `MOCK_ME`.
2. **Badges** — small icons next to the username (e.g. "Founding learner",
   "100-day streak"). Rendered inline by `UsernameDisplay`.
3. **Accents** — the gradient behind a profile card, the colour of a
   stat-chip on the social header. Theme-scoped, not Tailwind-direct.

## Catalog + rarity

Each cosmetic lives in a per-language-agnostic catalog with a `rarity` tag:

- Common — earned through default play (e.g. "Bronze tier ring").
- Rare — League-locked or quest-chain locked.
- Epic — Lingot shop purchases.
- Legendary — Season pass + one-off founders' items.

Catalog schema:

```jsonc
{
  "id": "frame.gold-pulse",
  "slot": "frame",
  "rarity": "epic",
  "preview": "/cosmetics/frame-gold-pulse.svg",
  "name_key": "cosmetics.frame.goldPulse.name",
  "unlock": { "kind": "shop", "cost_lingots": 600 }
}
```

Unlock kinds: `shop`, `quest_chain`, `league_reward`, `season_pass`,
`achievement`, `grandfathered`.

## Backend storage

- `cosmetics_catalog` (read-only seed) — id, slot, rarity, unlock metadata.
- `user_cosmetics` table — `(user_id, cosmetic_id, owned_at)` rows for
  "owned". One row per ownership.
- `users.equipped_cosmetics` JSON blob (or three columns if we want
  schema-strict): `frame_id`, `badge_id`, `accent_id`. JSON blob is
  cheaper; column form indexes for queries but we don't query by
  equipment.

The "owned vs equipped" distinction matters: a user may own three frames
and equip one. We do not auto-equip on unlock except for the very first
cosmetic the user earns of each slot.

## Surfaces

Where cosmetics render today and where they need to land:

- Social header avatar — `MOCK_ME.frame` (mock). After backend, read from
  `equipped_cosmetics.frame_id` resolved against the catalog.
- Leaderboard rows — frame on every entry's avatar.
- Public profile — frame + badges + accent.
- Lesson UI top-right "you" indicator — frame only (perf-light).
- Cosmetics inventory page — new, lives under `/<lang>/cosmetics`.

## API surface

- `GET /api/core/v1/users/me/cosmetics` — `{ owned: [...], equipped: {...} }`.
- `POST /api/core/v1/users/me/cosmetics/equip` body `{ slot, cosmetic_id | null }`.
- `POST /api/core/v1/shop/cosmetics/{id}/purchase` — debits `users.lingots`,
  inserts `user_cosmetics`. (Reuses the in-flight `/shop/purchase` skeleton.)

## Migration

1. Land the catalog + tables. Seed the catalog from a static JSON file.
2. Backfill the "default" frame ownership for every existing user (every
   account gets `frame.none` and `frame.bronze` for free).
3. Move `MOCK_ME.frame` to be loaded from the API. Keep the mock value as
   a fallback while the loader is mid-flight.
4. Roll over the seed for Trevor to grant him a few rare frames so the
   inventory screen has something to show.

## Out of scope (v1)

- Cosmetic crafting (combining low-rarity items).
- Time-limited rotations.
- Trading between users.
