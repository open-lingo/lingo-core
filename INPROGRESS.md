# IN PROGRESS — coordination note for Spencer (2026-05-25)

Multiple parallel agents are working across `lingo-core` and `lingo`. This is a snapshot so you don't override anything while picking up work.

## On `main` (pushed) — safe to build on

Most recent commits (lingo-core):

| SHA | What |
|---|---|
| `c529eb1` | Refactor users/admin/community routers onto `api_error` + `require_repo` |
| `db74cfc` | Refactor decks + stories routers onto `api_error` / `require_repo` |
| `4ea97fc` | Add `api_error` context manager + `require_repo` helper |
| `57a1027` | feat(social): reactions + spotlight + invites + threads + quest-targets |
| `dba33cd` | checkpoint: in-progress backend work + expanded seed |

Lingo (frontend) main is at `98793d1` with full social wiring + community profile previews + mobile sidebar fix.

## Active worktrees (DO NOT touch these files in main)

### `lingo-core/.claude/worktrees/agent-a844cee3d8ab97bc2`
**Status:** In flight — API refactor pass.
**Owns:** Continuing the `api_error` / `require_repo` rollout. May touch any router.

### `lingo-core/.claude/worktrees/agent-aed4b0d1dbaa62813`
**Status:** In flight — API refactor pass #2.
**Owns:** `app/admin/router.py`, `app/community/router.py`, `app/users/router.py` (has uncommitted edits) + `tests/test_api_error_sad_paths.py` (new). Applying the refactor patterns across more routers.

### Agents dispatched but still running (will land any time)

- **Backend bug-fix + Quests API + design docs** — touching `app/social/router.py` (CORS), `app/main.py` (CORS config), `app/config.py` (CORS_ORIGINS), `app/quests/` (NEW), `app/db/{protocols,sqlite,dynamo}/quests.py` (NEW), `scripts/seed.py` (adds `SEED_QUESTS`), plus `lingo/src/shared/api/social.ts` (leaderboard URL fix) and `lingo/src/features/social/components/{SocialHeader,UserAvatar}.tsx` (profile pic fix). Will also drop 3 design docs in `lingo/docs/` or `lingo-core/docs/`: leagues, XP curve, cosmetics.
- **Half-implemented audit + E2E verification** (`lingo` only) — sweeps `MOCK_/TODO/FIXME` across social/community/profile/quests/learn/home. Owns the Add-Friend root-cause fix + leaderboards page wiring + Message-button stub thread. Explicitly told NOT to touch `SocialHeader.tsx`, `UserAvatar.tsx`, `src/shared/api/social.ts`, `ContributorsPage.tsx`, or mobile-layout files.
- **Mobile scaling pass** (`lingo` only) — owns `src/routes/Layout.tsx` mobile header collapse + sweep across social/community/learn pages. Uses existing `useViewport`/`Show`/`Sheet`/`FilterBar` primitives.
- **Friend discovery + real community users** — adds `GET /api/core/v1/users/discover` (BACKEND), new `lingo/src/features/community/PeoplePage.tsx`, rewires `ContributorsPage` to seeded users, threads `maintainerUsername` through community deck cards. Will append the new backend endpoint to the END of `app/users/router.py` to minimize merge conflict with `aed4b0d1`. May extend `scripts/seed.py` for deck author attribution.

## Spencer — safe areas to grab right now

- **Anything in `lingo-core/app/srs/`, `app/decks/`** (except `app/decks/schemas.py` which the friend-discovery agent may touch to add `maintainerUsername`).
- **`app/progress/`** — not currently owned by any agent.
- **`app/community/`** persistence — `MockCommunityRepository` is wired for all backends per CLAUDE.md, this is wide open.
- **`app/stories/` Dynamo impl** — still missing.
- **Admin role enforcement** — `is_admin()` always true, untouched.
- **Lessons content** — `lingo/src/features/lesson/data/mock-ja-*.ts` curriculum files, none of the agents touch lesson content.
- **Tests** — coverage is thin everywhere outside social, easy place to add.

## DO NOT touch (will create merge conflicts with running agents)

- `lingo-core/app/social/router.py` (CORS fix agent + may regress reactions endpoint)
- `lingo-core/app/main.py` + `app/config.py` (CORS)
- `lingo-core/app/users/router.py` (api-refactor #2 + friend-discovery agent appending new endpoint)
- `lingo-core/scripts/seed.py` (backend agent + friend-discovery agent both adding seed blocks)
- `lingo-core/app/quests/` (new dir being created by backend agent)
- `lingo/src/shared/api/social.ts` (leaderboard URL fix)
- `lingo/src/features/social/components/SocialHeader.tsx` + `UserAvatar.tsx` (profile pic fix)
- `lingo/src/features/community/ContributorsPage.tsx` + `PeoplePage.tsx` (friend-discovery agent)
- `lingo/src/routes/Layout.tsx` + sidebars/topbars (mobile agent)
- `lingo/src/features/social/hooks/useSocial.ts` + `useSocialMutations.ts` (audit agent)

## What's deferred / post-MVP

- **All ad/finance work** — user decided MVP is ad-free for the trial. `src/features/ads/`, `src/features/adFree/`, `docs/ADS_*`, `docs/finance-*`, finance API endpoints, ad-density modulation. Code stays; UI surfaces should be hidden at launch.
- **`refactor/ui-primitives-consolidation`** (worktree still alive on lingo) — has the modal-stack migration commit (`a7690d5`) that wasn't merged. Legacy `ConfirmModal` / `ModalBase` / `ModalBackdrop` still ship alongside the new `Modal` / `Dialog`. Pull the commit when ready to consolidate.

## Open MVP punch list

See `lingo/docs/mvp-alignment-review-2026-05-25.md` for the full doc. Top must-ships:

1. Korean content scope decision (JA-only or 2-3 week KO push)
2. Fix 5 failing backend social tests (verify these still exist post-refactor)
3. Hide/label any remaining mock-driven UI (home rail, leaderboards, contributors)
4. Staging environment
5. Prod Auth0 + `DEBUG=false` guard
6. Rate limiting (`slowapi` on sync/decks/users)
7. Sentry on both sides

## How to coordinate

- If you grab work that touches a "DO NOT touch" area, ping me before commit so we can sequence.
- Anything outside the do-not-touch list, full speed ahead.
- This file is a snapshot. Delete it when agents finish merging.
