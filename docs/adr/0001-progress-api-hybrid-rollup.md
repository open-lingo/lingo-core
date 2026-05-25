# ADR-0001: Progress API — Hybrid Rollup with Login-Triggered Concept Recompute

**Status:** Accepted (2026-05-19)
**Authors:** Trevor Lichfield
**Supersedes:** none
**Related:** `app/srs/` (existing SRS state, separate concern) · `docs/PRODUCT_BACKLOG.md` (lingots/cosmetics economy) · `lingo/docs/ECONOMICS.md` (cost discipline)

---

## Context

Open Lingo needs server-side progress tracking for lessons, modules, and per-concept mastery. The system must:

- Validate lesson attempts server-side (correct answers must not ship in the client bundle — current `mockLessons.ts` exposes them, that's the existing cheat surface)
- Track per-attempt step results so "where do users struggle" surfaces (heatmap, suggestions) can be built later
- Maintain streak, XP, and lingots (in-app currency) atomically per attempt
- Stay cheap at $0.001–0.01 per active user per month on Dynamo to fit the broader cost target ([ECONOMICS.md](../../../lingo/docs/ECONOMICS.md))
- Be extensible enough to feed the Learn-page skills heatmap, home-page weekly sparkline, and per-concept drill recommendations without expensive on-render compute

Three architectures were on the table.

### Alternative A — All eager (transactional rollups on every attempt)

Each lesson-complete handler writes (in a single `TransactWriteItems`):

- 1 attempt row
- 1 lesson-best rollup
- N concept rollups (~5–10 per attempt)
- 1 day rollup
- 1 streak row

Total: ~10–15 transactional items per attempt at **2× WCU each** = ~20–30 WCU.

- **Pro:** all rollups always fresh; page renders hit pre-computed rows (3 RCUs each); reads are dirt cheap.
- **Con:** ~$0.0116 per active user per month in writes. Transaction size grows with concept count per lesson. Dynamo `TransactWriteItems` has a hard limit of 100 items.

### Alternative B — All lazy (compute everything from attempt log on read)

Each attempt writes only the attempt row + user-row update (streak/XP/lingots). All rollups computed on-demand by querying the attempt log via GSI and aggregating in Lambda.

- **Pro:** ~$0.0049 per user-month. Writes are minimal. No rollup-row maintenance.
- **Con:** Reads expensive at lifetime scale. A user with 365 days × 5 lessons/day = 1,825 attempts × 2KB = ~3.6MB to scan for a single concept heatmap render. ~900 RCUs per scan. Page latency suffers.

### Alternative C — Hybrid (this ADR's choice)

Split rollups by *cost-to-update* vs *frequency-of-read*:

| Rollup | Strategy | Why |
|---|---|---|
| Streak, XP, lingots, level | **Eager** on user-row | Single `UpdateItem`, 1 WCU. Read on every authenticated page load. Must be fresh. |
| Day activity (lessonsCompleted, minutesActive) | **Eager** on progress table (`SK = DAY#YYYY-MM-DD`) | 1 WCU per attempt. Read on every home-page load (week sparkline). Cheap to keep fresh. |
| Lesson best-score (`SK = LESSON#<lessonId>`) | **Eager** on progress table | Bounded set (~100 lessons per user). 1 WCU per attempt. Used for Learn-page progress card. |
| **Concept mastery (`SK = CONCEPT#<conceptId>`)** | **Lazy** — invalidated on attempt, recomputed on read | The expensive one. Scans recent attempts to compute. Per-attempt cost: just set `staleAt = now()`. Per-read cost: only when stale (rare for active users, never for inactive ones). |

Per-attempt write cost: ~4 non-transactional `UpdateItem` calls + N tiny "invalidate" writes = ~$0.001 per user-month.

### Alternative D — All lazy, computed in background by a scheduled job

Every N hours / days, a Lambda job sweeps all users and updates rollups. Same data shape as B but with scheduled recompute.

- **Pro:** smooths cost (compute happens off-peak), users get fresh rollups without paying read latency
- **Con:** Always recomputes — even for users who didn't log in. Wasted compute on dormant accounts. Plus background-job ops complexity (EventBridge, dead-letter queues, idempotency).

---

## Decision

**Adopt Alternative C (Hybrid).** Specifically:

1. **Hot user-owned stats live on the existing user row** (`lingo_users`, `PK = USER#<id>`, `SK = PROFILE`): `streak`, `bestStreak`, `lastActiveDate`, `xp`, `level`, `lingots`. Updated with a single `UpdateItem` per attempt (atomic counters via `ADD`).

2. **Progress table** (`lingo_progress`) has four SK shapes under each user PK:

   ```
   SK = ATTEMPT#<lessonId>#<isoTs>    # immutable per-attempt log (source of truth)
   SK = LESSON#<lessonId>             # eager: best score, attempt count, latest attempt time
   SK = DAY#<YYYY-MM-DD>              # eager: lessonsCompleted, minutesActive, xpEarned
   SK = CONCEPT#<conceptId>           # LAZY: recomputed on read when staleAt != null
   ```

3. **One GSI: `UserAttempts-Index`** with `user_id` (S) hash + `attemptedAt` (S) range. Used for (a) "recent attempts" feed queries and (b) the lazy concept rollup recompute. Sparse (only attempt rows have `attemptedAt`).

4. **Per-attempt write flow** (non-transactional — see consequences for the consistency trade-off):

   ```
   1. PutItem ATTEMPT row
   2. UpdateItem user row (ADD xp, ADD lingots, conditional streak update, set lastActiveDate)
   3. UpdateItem LESSON row (ADD attemptCount, conditional bestScore update)
   4. UpdateItem DAY row (ADD lessonsCompleted, ADD minutesActive)
   5. UpdateItem each CONCEPT row touched by the attempt: set staleAt = now()
      (no recompute work — just invalidation flags)
   ```

5. **On read** (concept heatmap, skill suggestions):

   ```
   - Query SK begins_with CONCEPT# for the user
   - For each row where staleAt is set:
       - Query attempt log for last N attempts via UserAttempts-Index
       - Recompute encounters / correctCount / recentResults / avgDurationMs
       - PutItem refreshed row with staleAt = null
   - Return the rollups
   ```

6. **Login trigger** (the MVP recompute mechanism): on every authenticated login (or first authenticated request of a session), the auth dependency checks the user's `lastActiveDate` and triggers a concept-rollup refresh if it's older than the staleness threshold (24h initially — tunable).

7. **Answer-key storage**: lesson curriculum stays in the frontend `mockLessons.ts` for now. A build-time script (`lingo/scripts/build-answer-keys.mjs`) extracts the correct-choice map and writes `lingo-core/app/curriculum/answers/<lessonId>.json`. Both repos build from the same source. The client bundle gets a sanitizer step that strips `correct_choice` fields from each lesson step before shipping.

---

## Consequences

### Positive

- **Per-attempt cost stays near $0.001 per active user per month.** Hybrid is 12× cheaper than all-eager and 5× cheaper than all-lazy at our scale.
- **Read-hot surfaces (home, learn pages) stay fast** because the rollups they need are pre-computed and tiny (~10KB total per user for all lesson + day + user-row stats).
- **The expensive concept rollup is only paid when someone actually looks at the heatmap.** Inactive users cost nothing.
- **Source of truth is the attempt log.** Rollups are derived and rebuildable. If a rollup is wrong, the system self-heals via the lazy-recompute path.
- **No background-job infrastructure required for MVP.** Compute runs inside the request that triggered it.

### Negative / risks

- **Non-transactional writes mean partial-failure windows.** If the Lambda dies after writing the attempt row but before the user-row update, the user briefly sees stale XP. Next attempt's update fixes it. Acceptable for a learning app (no monetary stakes), but explicit. If we ever need stronger guarantees (real-world money on the line), switch the per-attempt flow to `TransactWriteItems` — adds 2× WCU cost but small.
- **Login latency: the concept rollup recompute happens synchronously in the auth handler.** For users with hundreds of attempts, recompute is ~50–150ms. Acceptable initially. The evolution plan below addresses this.
- **Concept tagging requires curriculum authoring discipline.** Every lesson step needs `conceptIds: [...]`. Untagged steps don't contribute to any rollup — data accrues only for tagged content. Strategy: write a small concept taxonomy doc (`lingo-core/docs/CONCEPT_TAXONOMY.md`, ~30–50 concepts per language), tag alphabet + first 5 grammar lessons in the initial pass, lazy-tag the rest as we touch them.
- **GSI write amplification:** every attempt write also writes to `UserAttempts-Index`. Doubles attempt-write cost. At the modeled scale this is still negligible (~$0.000375/user/month for the GSI).

---

## Evolution path

The MVP runs concept recompute **synchronously inside the login (or first-authed-request) handler**. This works at our current scale but degrades as the attempt log grows per user. Documented evolution:

### Phase 2 — Last-login-staleness check (no SQS yet)

When `staleAt` is set on a concept row AND `lastActiveDate` is recent (user has been engaging), trigger an in-process recompute on the next read. Skip recompute for cold users entirely until they come back.

This is essentially what the hybrid already does. No infra change required. Document it as the baseline behavior in the recompute logic.

### Phase 3 — SQS dispatch + background Lambda processor

When recompute starts hurting login latency (~ >200ms p95) OR we want batched compute (e.g. nightly aggregations across users):

1. The login handler stops doing the recompute inline. Instead it enqueues an SQS message: `{ userId, staleConceptIds: [...], requestedAt }`.
2. A separate Lambda (`progress-rollup-worker`) consumes the queue and does the recompute work.
3. The login response returns immediately with the *stale* rollups (or "loading" sentinel) — the next read after the worker finishes gets fresh data.
4. Idempotency: each message has a `requestedAt`; if the worker sees a `staleAt < requestedAt` after acquiring the row, it skips (someone else already processed).
5. Dead-letter queue handles repeated failures.

**Tripwire for Phase 3:** when concept-recompute p95 latency exceeds 200ms in the auth/login handler. Re-evaluate.

### Phase 4 — Scheduled background aggregation (only if needed)

If we ever need cross-user analytics (admin dashboard: "what's the hardest lesson", "what concept has the lowest retention across all users"), add an EventBridge-scheduled Lambda that runs nightly. This is **distinct** from per-user rollups; it's pre-computing org-level metrics.

Not needed for MVP. Document as a known extension point.

---

## Sync model — batch, not per-event (matches SRS)

**Decision (revising the initial single-attempt-POST design):** lesson completions are
buffered client-side and synced in batches, exactly like SRS card state today.
No write per lesson completion. The user can finish 10 lessons in a session and
that's still **one** API call when the SyncManager flushes the buffer.

Why:

- Per-event writes balloon Dynamo WCU spend at scale. A burst of completions
  (a power user grinding through a module) shouldn't be a burst of writes.
- The SRS sync UX already exists (`SRSPendingSync`, `useSRSSyncSource`,
  `SyncManager` panel) and users know it. Lessons should join that menu.
- Buffer survives page reload, so a lesson finished offline isn't lost when
  the user closes the tab before reconnecting.

Sync triggers (same as SRS):

1. **Manual** — user clicks the header cloud (dirty/error) or "Sync now" in the SyncManager popover (`cloud` / `cloudSync` / `cloudAlert` states; see `lingo/docs/handoff-2026-05-24-home-sync-ux.md`)
2. **Periodic** — auto-flush every N minutes when the buffer is non-empty and
   the user is online
3. **On exit** — `beforeunload` handler attempts a `navigator.sendBeacon` flush
4. **On login / session start** — push buffered attempts before fetching latest
   server state

### Trade-off: when does answer validation happen?

Validating each step server-side requires per-step round-trips, which kills
the batch benefit. The pragmatic answer:

- **Phase 1 (MVP — ship this):** client validates against the client-side answer
  key (current behavior — `correct` fields already ship in `mockLessons.ts`).
  Buffered results include `stepResults` already-graded. Server stores them
  verbatim, applies sanity / rate / prerequisite / idempotency checks but does
  not re-validate answers. The threat model documented in this ADR remains
  weak (no monetary stake), so this is acceptable.

- **Phase 2 (later — when stakes appear):** answer keys move out of the client
  bundle to a server-side store (`app/curriculum/answers/`). The sync endpoint
  switches from "trust client-graded results" to "re-grade server-side from
  user choices + stored answers." Same batch shape; just different validation.
  Migration is one router change + one frontend change to send `choices`
  instead of pre-graded `stepResults`.

The ADR previously called for Phase 2 from day one; we're explicitly deferring
it to keep Phase 1 cost-discipline + UX consistency with SRS.

### Streak check: client-driven, not per-attempt

The user-row streak update (`streak`, `bestStreak`, `lastActiveDate`) is the
single most expensive write in the per-attempt flow: a `GetItem` to read the
current `lastActiveDate` followed by a conditional `UpdateItem`. Running it
on *every* batch sync is wasteful — once the streak has ticked for the day,
nothing about it changes until the user crosses local midnight.

**Decision:** the client owns "is this the first sync of a new local day?"
The batch payload carries an explicit `checkStreak: bool`:

- The client stores `open-lingo-last-streak-sync` (YYYY-MM-DD in the user's
  local timezone) in localStorage. See `src/features/lesson/engine/sessionStreak.ts`.
- On building a batch payload, `checkStreak = true` iff the stored date is
  absent or doesn't match today.
- On a successful response, the client writes today's date back to that
  key. Every subsequent same-day sync sends `checkStreak: false` and the
  server skips the streak path entirely.

The server trusts the flag. It is an optimisation hint, not a security
boundary — there is no monetary stake, the worst case is one missed streak
tick (recovered on the next true → false transition) or one wasted cheap
UpdateItem. The streak field stays atomic and the XP / lingots / day-rollup
writes still happen per attempt regardless of the flag.

Auth0 SDK note: `@auth0/auth0-react` exposes no SDK-level "fresh login vs.
silent renew" callback (verified against `node_modules/@auth0/auth0-react/dist/`).
The closest signal is an `isAuthenticated` false→true transition, which the
SDK fires for both kinds of token acquisition. Time-based gating in
localStorage is therefore the primary mechanism. A future enhancement may
layer the transition signal *additively* on top — clearing the marker on a
detected fresh login so the next sync re-checks streak — but the
localStorage marker remains the source of truth for "have we checked today."

## Endpoint shape

```
GET  /api/core/v1/curriculum/lessons/:lessonId
  Returns lesson body. In Phase 1, ships with correct_choice fields (existing
  behavior). In Phase 2, sanitized.

POST /api/core/v1/progress/lessons/batch
  Body: {
    attempts: [
      {
        clientAttemptId, lessonId, attemptedAt, durationSec, passed, score,
        stepResults: [{stepIdx, conceptIds, correct, durationMs}]
      }, ...
    ],
    checkStreak: bool   # true iff first sync of a new local day; server
                        # only runs the streak GetItem+UpdateItem path
                        # when true. See "Streak check" section.
  }
  Response: { results: [{
    clientAttemptId, attemptId, accepted: bool, reason?: str,
    xpEarned, streakAfter, lingotsEarned, dailyTotalLessons
  }, ...] }

  Server processes attempts in order. Each:
    1. Idempotency check on clientAttemptId
    2. Sanity (durationSec floor/ceiling)
    3. Prerequisite check
    4. Persist attempt + rollup updates (per the hybrid flow below)
    5. Return per-attempt result

GET  /api/core/v1/progress/me
  Aggregate for page render. Includes lessons[], concepts[] (lazy-recomputed on this call if stale), last30days, user stats.

DELETE /api/core/v1/progress/me
  Wipe all progress rows for the user (attempts + lesson/day/concept rollups) and reset
  user-row stats (streak, XP, level, lingots, lastActiveDate). Used by Learn **Start over**.
  Client also calls `DELETE /api/core/v1/srs/all` in the same flow.

GET  /api/core/v1/progress/me/attempts?lessonId=&limit=20&cursor=
  Paginated attempt history. Without lessonId → uses UserAttempts-Index sorted by recency.

POST /api/core/v1/progress/me/touch
  Lightweight endpoint hit on login or session-start. Returns user stats + triggers concept-rollup staleness check. Used by frontend right after Auth0 token acquisition.
```

## Client buffer + sync source

Mirrors `src/features/flashcards/engine/srsSync.ts`:

- `src/features/lesson/engine/lessonStorage.ts` — per-user keys:
  `open-lingo-lesson-attempts:v1:{userId}`, `open-lingo-lesson-step-events:v1:{userId}`
- `src/features/lesson/engine/lessonSync.ts` — step events + pending attempts;
  mid-lesson **draft** attempts use stable id `draft:{lessonId}` (idempotent re-push)
- `recordStepEvent()` — append step outcome, upsert draft, notify SyncManager
- `getLessonDirtyCount()` / `isPendingAttemptDirty()` — drafts stay in the buffer
  after a successful sync (`syncedAt`); dirty again when `bufferedAt > syncedAt`
  (new graded step). Step events for a lesson with any pending row are not counted
  as orphans.
- `materializeOrphanDrafts()` — before building a batch, ensure step events have
  a draft row so sync does not no-op while the UI still shows dirty
- `performLessonSync()` — on accepted **draft**, mark `syncedAt` (do not remove);
  on accepted **final** attempt, remove from buffer and clear step events for that lesson
- `src/features/lesson/useLessonSyncSource.ts` — `SyncSource` for SyncManager
- `src/features/lesson/useLessonSyncSession.ts` — 30s interval + unmount flush on
  `LessonPage` / `AlphabetLessonPage`
- `src/features/lesson/LessonProgressHydrate.tsx` — app-wide flush after `GET /progress/me`
- `src/features/sync/SyncManagerTrigger.tsx` — registers lessons + SRS sources
- `src/shared/components/sync/SyncManager.tsx` — dirty = warning + `cloudSync` on
  hover; failure (`cloudAlert`) only when `onSyncNow` throws (not stale dirty count)

Result: Sync Manager shows a **Lessons** row (when authenticated) with the same
manual / periodic sync behavior as SRS.

### Dev: inspect progress JSON

With dev unlock (`?dev=1` or DEV panel), Learn page exposes **`</>` Progress JSON**:
`GET /progress/me` payload plus local completion cache (`getLocalLessonProgressSnapshot`).
Component: `src/features/learn/components/LearnProgressJsonOverlay.tsx`.

---

## Rate limiting + sanity rules (server-side, enforced in router)

- `slowapi`: max 10 attempt submissions per user per minute
- `durationSec >= max(5, stepCount * 0.5)` — catches 0-second cheats
- `durationSec <= 3600` — catches abandoned tabs that "complete" 24h later
- `clientAttemptId` (UUID from client) is unique per attempt → idempotent retries safe
- Prerequisite check (loose): previous lesson in module must have at least 1 attempt row. Encourages forward motion without blocking exploration.
- WAF rate-based rule (already planned in infra) catches IP-level abuse independently.

---

## Out of scope for this ADR

- **XP earning rules.** Constants (`EARN_RULES`) defined later in `app/progress/xp.py`. Tunable without DB changes.
- **Streak forgiveness / "freeze a day"** mechanic. Backlog. Reads the same streak field; just decides differently when to reset.
- **Lingots earning beyond attempt completion** (deck approval, weekly bonuses). Backlog.
- **Cross-user leaderboards.** Would need a different table or aggregator. Not now.

---

## References

- DynamoDB pricing analysis: `lingo/docs/ECONOMICS.md` § "DynamoDB cost analysis"
- Frontend mock progress data being replaced: `lingo/src/shared/domain/mockProgress.ts`
- Existing SRS pattern to mirror for repo structure: `app/db/dynamo/srs.py`
