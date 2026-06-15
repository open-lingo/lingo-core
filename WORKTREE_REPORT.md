# C3 — Verify the whole stack is really on DynamoDB

**Verdict: the stack is fully on DynamoDB-capable repos.** Three genuine
gaps were found and fixed; the rest of lingo-core was already real Dynamo
(the provider's "stub raises NotImplementedError" comments were stale — code
beat the docs, as the task warned). Loud-failure guards now prevent any
domain from silently degrading under `DB_BACKEND=dynamodb`.

## Method

For every domain repo, under `DB_BACKEND=dynamodb`:
1. AST-audited each `Dynamo*Repository` against its Protocol — every method
   present, no body that is a silent stub (`pass` / `return None` /
   `return []` / `raise NotImplementedError`).
2. Confirmed the provider wires each domain to a real `Dynamo*Repository`
   (no Mock, no SQLite fallback, no silent `None`).
3. Cross-checked prod env (`DB_BACKEND`/`EVENT_LOG_BACKEND`) and Terraform
   (table provisioned + IAM grant present) so "the repo works" actually maps
   to "the domain works in prod".

## Per-domain status — lingo-core (`DB_BACKEND=dynamodb` in prod)

| Domain | Live Dynamo repo? | File | Notes |
|---|---|---|---|
| users | ✅ | `app/db/dynamo/user.py` | `user_stats` is a Scan (perf note, not a stub) |
| subscriptions | ✅ | `app/db/dynamo/subscription.py` | |
| srs | ✅ | `app/db/dynamo/srs.py` | |
| decks | ✅ | `app/db/dynamo/deck.py` | |
| deck_votes | ✅ | `app/db/dynamo/deck.py` (votes table) | wired via `votes_table_name` |
| progress | ✅ (fixed) | `app/db/dynamo/progress.py` | `update_attempt_steps` was a `return None` no-op → now UpdateItem |
| stories | ✅ | `app/db/dynamo/story.py` | |
| quests | ✅ | `app/db/dynamo/quests.py` | |
| social | ✅ | `app/db/dynamo/social.py` | stale "stub" comment removed |
| leaderboard | ✅ | written by lingo-async `leaderboard/updater.py` | direct UpdateItem |
| tags | ✅ | `app/db/dynamo/tag.py` | stale "stub" comment removed |
| community (×5) | ✅ | `app/db/dynamo/community.py` | Mock is sqlite-connect-failure fallback only |
| admin_audit | ✅ | `app/db/dynamo/audit.py` | stale "stub" comment removed |
| platform_settings | ✅ | `app/db/dynamo/platform_settings.py` | |

## Per-domain status — lingo-ops (`DB_BACKEND=dynamodb` in prod)

| Domain | Live Dynamo repo? | File | Notes |
|---|---|---|---|
| finance | ✅ (fixed) | `app/db/dynamo/finance.py` | was a `NotImplementedError` stub → real impl. Finance was **silently 503'ing in prod**. |
| jobs | ✅ | `app/db/dynamo/jobs.py` | already real |
| events (read) | ⚪ disabled by design | `app/db/provider.py:get_events_repo` | returns `None` unless `EVENT_LOG_BACKEND=sqlite`; off in prod, returns empty (not 503). Dynamo read-side is a deliberate future feature. |

## Per-domain status — lingo-async (`EVENT_LOG_BACKEND` unset in prod)

| Domain | Live Dynamo repo? | File | Notes |
|---|---|---|---|
| events (write) | ✅ (fixed) | `app/db/dynamo/events.py` | was a `NotImplementedError` stub → real impl. Still disabled by default (`EVENT_LOG_BACKEND` unset). |
| leaderboard | ✅ | `app/leaderboard/updater.py` | direct UpdateItem on `lingo_social_leaderboard` |
| user (read) | ✅ | reads `lingo_users` directly | |
| quests eval | ⚪ stub by design | `app/quests/evaluator.py` | documented future feature; not a storage-backend gap |

## What was stubbed and fixed

1. **lingo-core `DynamoProgressRepository.update_attempt_steps`** — returned
   `None` (no-op). Draft mid-lesson step state silently dropped in prod
   (cross-device lesson recovery broken; no XP impact). Now an `UpdateItem`
   on the `CLIENT#` item, guarded by `attribute_exists(PK)`.

2. **lingo-ops `DynamoFinanceRepository`** — every method raised
   `NotImplementedError`. Because lingo-ops runs `DB_BACKEND=dynamodb`, the
   finance domain degraded at startup and every finance read endpoint 503'd
   in prod. Implemented `upsert`/`get`/`list_for_source`/`list_sources`
   against a new `lingo_finance_snapshots` table (PK=`SOURCE#`, SK=`KEY#`).

3. **lingo-async `DynamoEventsWriteRepository`** — `save`/`update_status`
   raised `NotImplementedError`. If `EVENT_LOG_BACKEND=dynamodb` were ever
   set, every processed event would have DLQ'd. Implemented against a new
   `lingo_events_log` table (PK=`USER#`, SK=`EVENT#<received_at>#<id>`, GSI
   `EventId-Index` on `id`, TTL `ttl_epoch`). Kept disabled by default.

## Infra (lingo-infra/main.tf) — flagged for review

- New `aws_dynamodb_table.finance_snapshots` (Domain=ops, PAY_PER_REQUEST,
  no GSI) + `FinanceSnapshotsCRUD` IAM statement on the ops Lambda role.
- New `aws_dynamodb_table.events_log` (Domain=async, PAY_PER_REQUEST, GSI
  `EventId-Index`, TTL `ttl_epoch`) + `EventLogWrite` IAM statement on the
  async Lambda role.
- Both are new tables (not reshards of live ones) and PAY_PER_REQUEST, so
  effectively zero standing cost. No env-var flips: events log stays
  disabled until `EVENT_LOG_BACKEND` is set.

## The guard that now prevents regression

- **lingo-core / lingo-ops** `provider._assert_backend_wiring()` runs at the
  end of `init_repositories()`. It fails loudly (RuntimeError at startup) if
  any required domain resolved to a non-`Dynamo*` class under the dynamodb
  backend, or is silently `None` without being recorded as a runtime
  connect failure (`_degraded`).
- **lingo-core** `tests/test_dynamo_conformance.py` — static AST gate
  asserting every domain's Dynamo repo implements its full Protocol with no
  silent-stub bodies, plus a wiring test that boots `init_repositories()`
  under `DB_BACKEND=dynamodb`.
- **lingo-ops** `tests/test_finance_dynamo.py` — moto round-trip + a
  `test_no_method_raises_not_implemented` regression guard.
- **lingo-async** `tests/test_events_repo_dynamo.py` — fake-table behavior
  test + provider test asserting no method is a `NotImplementedError` stub.

## Test results

- lingo-core: full suite green (272 + 13 new conformance = 285).
- lingo-ops: 47 green (incl. new finance Dynamo).
- lingo-async: 67 green (incl. new events Dynamo).
- `terraform fmt -check` + `terraform validate`: clean.
