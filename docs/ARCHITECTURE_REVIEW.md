# Architecture Review — Backend (`lingo-core`)

_Reviewed: 2026-05-18, against CET Dashboard org architecture standards (see `gitlab-profile` reference doc set)._

This is a conformance audit. Standards were ported from an MUI/FastAPI org doc — the `api_error` context manager doesn't exist here yet but the *principle* (centralized error wrapping) does and should.

## Snapshot

- FastAPI 0.115 + Uvicorn (dev) / Mangum (Lambda)
- Python 3.13, async-first throughout
- Auth0 RS256 JWT w/ JWKS caching + `X-Dev-User` debug bypass
- Pluggable repository pattern: SQLite (`aiosqlite`) for dev, DynamoDB (`aioboto3`) for prod, single-table + 3 GSIs
- Ruff configured; pytest configured with `asyncio_mode = "auto"`

Architecture is well-designed — Protocol-based repos, clean DI through `provider.py`, content-type handler registry for subscriptions, full type hints, Pydantic v2 models for all I/O.

## Conformance scorecard

| Standard | Status | Notes |
|---|---|---|
| Module-level singletons (clients/repos) | ✅ | `provider.py` hydrates at startup |
| Repository pattern w/ Protocols | ✅ | `app/db/protocols/` |
| Secrets cached module-level | ⚠️ | Config is, but no boto3 client singletons audited |
| `datetime.now(timezone.utc)` not `utcnow()` | ✅ | Needs spot-check across all routes |
| UUID validation via `Path(...)` annotation | ⚠️ | Pattern not enforced consistently |
| Centralized error handling (`api_error`-equivalent) | ❌ | 158 scattered try/except + HTTPException |
| Linting | ✅ | Ruff (E/F/I/UP) |
| Test coverage | ❌ | One smoke test (`test_fastapi_app_metadata`). Effectively 0% |
| No dead/stub repos in prod path | ❌ | `MockCommunityRepository` (in-memory) is wired for all backends |
| Admin role enforcement | ❌ | `app/auth/roles.py` disabled, TODO for OAuth scopes |

## High-priority gaps

### 1. Test coverage is one assertion
`tests/test_smoke.py` validates app metadata. Critical paths — user registration, SRS sync delta-merge, deck approval flow, subscription handlers — have zero coverage. The org's `code-optimizer` and `perf-optimizer` agents both **assume a test baseline** to refactor against. Without it, any refactor is freehand. Highest-leverage fix.

Recommended scaffolding:
- `tests/conftest.py` with fixtures: `app_client`, `dev_user`, `clean_sqlite`
- One happy-path test per domain router (users, srs, decks, stories, community, admin)
- Pytest-asyncio is already configured

### 2. Community persistence is a mock
`MockCommunityRepository` (in-memory, reset on restart) is wired for **all** backends including Lambda. Forum threads, posts, votes, addons all evaporate on cold start. Both `app/db/sqlite/community.py` and `app/db/dynamo/community.py` have TODOs and unimplemented schemas. Pick one (SQLite for dev, Dynamo for prod) and implement.

### 3. Admin auth is disabled
`app/auth/roles.py` has `is_admin()` returning truthy with a TODO to implement OAuth scope enforcement. All 15 admin routes (`app/admin/router.py`) — ban/unban users, deck approval, community moderation — are effectively open to any authenticated user. Either implement scope checks or gate by allow-list user IDs until scopes land.

### 4. Error handling is scattered
158 instances of try/except + `HTTPException(...)` across the routers means error contracts drift per author. The org standard is a single `api_error("context")` context manager that wraps external calls and standardizes the HTTP error shape. Worth adding before more endpoints land.

### 5. Stories don't work on DynamoDB
`provider.py` leaves `_story_repo = None` for the Dynamo backend. Stories work in dev only. Either implement `app/db/dynamo/story.py` or surface a clear 503 when deployed.

## Medium-priority

- **Ruff `target-version = "py312"` but `requires-python = ">=3.13"`.** Inconsistency. Bump ruff target to py313.
- **Lambda cold starts (2–5s) acknowledged but unmitigated.** No warmer schedule, no provisioned concurrency wired. Acceptable for current scale; revisit before launch.
- **`DEBUG=true` disables JWT entirely.** Safe for dev, but document clearly that this must never be set in any deployed env. Consider asserting in `main.py` lifespan that `DEBUG=False` when running under Mangum.
- **UUID `Path(..., pattern=...)` validation.** Some routes copy-paste validation blocks. Extract a typed alias (`UUIDPath = Annotated[str, Path(pattern=r"^[0-9a-f-]{36}$")]`) and reuse.

## When adding features

Before merging a feature that touches a domain router, add at minimum one happy-path test for that router — set the baseline as features land rather than as a separate effort that gets deferred. Before merging a feature that touches community, implement at least the SQLite community repo so dev/staging behave like a real backend.

## Recent positives (since clone)

- `lingo-core.zip` removed from repo, `.gitignore` updated ✅
- `app/middleware/security_headers.py` added ✅
- `tests/test_smoke.py` baseline created ✅
- Finance router scaffolded under `app/finance/` ✅

## Reference

Source standards: `~/repositories/projects/dashboard/gitlab-profile/` — see `docs/claude/agents/{cleanup,code-optimizer,perf-optimizer}/` for the agent-enforced patterns.
