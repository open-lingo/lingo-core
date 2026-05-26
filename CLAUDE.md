# CLAUDE.md — `lingo-core` (Open Lingo backend API)

FastAPI service for user management, SRS progress, course/deck manifests, stories, community/forum, admin. Auth via Auth0 RS256 JWT. Deploys to AWS Lambda via Mangum.

## Critical orientation

- **Architecture audit + conformance gaps:** `docs/ARCHITECTURE_REVIEW.md` — read before structural changes.
- **Frontend client:** `../lingo/` — shares Auth0 tenant, talks to this API at `/api/core/v1/...`.
- **API base path:** `/api/core/v1` — versioned aggregator in `app/v1/router.py`.

## Stack

- Python 3.13 (async-first throughout)
- FastAPI 0.115, Pydantic Settings v2, Uvicorn (dev) / Mangum (Lambda prod)
- Auth: Auth0 RS256 JWT w/ JWKS caching. `X-Dev-User` bypass when `DEBUG=true`.
- DB: pluggable repos with two backends
  - SQLite (`aiosqlite`) for local dev
  - DynamoDB (`aioboto3`) single-table + GSIs for prod
- Linting: Ruff (E/F/I/UP), line-length 100. ⚠️ target = py312 but requires-python = py313 — inconsistent.
- Testing: pytest + pytest-asyncio (`asyncio_mode = "auto"`). One smoke test exists; coverage is effectively zero.

## Source layout

```
app/
├── main.py                 # app factory + lifespan + CORS
├── config.py               # Pydantic Settings (env vars, .env)
├── handler.py              # Mangum ASGI adapter for Lambda
├── auth/
│   ├── dependencies.py     # get_current_user, get_registered_user
│   ├── roles.py            # ⚠️  is_admin() NOT ENFORCED (TODO: OAuth scopes)
│   ├── ban.py
│   └── schemas.py
├── db/
│   ├── protocols/          # Protocol interfaces — repos depend on these
│   ├── provider.py         # DI singletons; init_repositories() at startup
│   ├── sqlite/             # 6 repos (user, srs, deck, subscription, community, story)
│   ├── dynamo/             # DynamoDB impls (community + story incomplete)
│   └── mock/               # MockCommunityRepository (⚠️ in-memory, wired for ALL backends)
├── v1/router.py            # mounts srs/users/decks/stories/community/admin under /api/core/v1
├── users/                  # router + schemas + subscriptions/ (content-type handler registry)
├── srs/                    # FSRS-6 modal sync (recognition + production, delta merge, last-write-wins by max lastReviewDate)
├── decks/                  # CRUD + batch fetch + admin approval
├── stories/                # ⚠️ DynamoDB impl missing (None in provider)
├── community/              # forum threads/posts/votes + addons + markdown
├── admin/                  # 15 routes (user ban, deck approval, mod) — ⚠️ all open w/o role enforcement
├── moderation/             # ban reason codes, appeal schemas
├── middleware/             # security_headers.py
└── finance/                # recent scaffold
```

## Conventions

- **Routes:** domain-oriented, one router per domain mounted in `v1/router.py`.
- **Repos:** all data access via Protocols in `db/protocols/`. Routers never import a concrete repo.
- **DI:** repos are module-level singletons, hydrated at startup by `app/db/provider.py`. Access via `provider.get_*_repository()`.
- **Schemas:** Pydantic v2 for all request/response bodies. Live next to the router (`<domain>/schemas.py`).
- **Async only:** every DB call awaits. No sync I/O in request paths.
- **Datetime:** use `datetime.now(timezone.utc)`, never `datetime.utcnow()`.
- **Imports:** top-level only. No conditional imports, no imports inside functions.
- **Logging:** `lingo.startup`, `lingo.auth`, `lingo.access` — structured per module.

## Auth flow

1. Frontend gets Auth0 JWT via `getAccessTokenSilently()`
2. Bearer token → `auth/dependencies.py:get_current_user` → validates against JWKS, returns internal User
3. `get_current_user_optional` returns `None` for public endpoints
4. `DEBUG=true` + `X-Dev-User: <auth0_sub>` bypasses JWT entirely (dev only — **must never be set in deployed envs**)

## What's missing (do NOT assume working in features)

- **Community persistence**: `MockCommunityRepository` is wired for all backends. Forum/addons data evaporates on Lambda cold start. SQLite + Dynamo impls are stubs.
- **Stories on Dynamo**: `_story_repo = None` in provider — stories work in dev only.
- **Admin role enforcement**: `is_admin()` returns truthy. All admin routes effectively open to any authed user.
- **Tests**: one smoke test (`tests/test_smoke.py`). Critical paths uncovered.

If a feature lands in any of these areas, address the gap first or document the workaround.

## Patterns to follow

- **External clients** (boto3 resources, HTTP clients): instantiate at module level, never per-request.
- **Secrets**: fetch once at module load (Pydantic Settings handles this for env vars).
- **UUID path params**: prefer `Annotated[str, Path(pattern=r"^[0-9a-f-]{36}$")]` typed alias over copy-pasted validation.
- **Error handling**: 158 scattered try/except + `HTTPException` calls today. Centralize via an `api_error("context")` context manager before adding more endpoints.

## Dev loop

```bash
# install
pip install -e ".[dev]"

# run (defaults to SQLite at ./lingo.db)
uvicorn app.main:app --reload --port 8000

# seed fixtures
python -m scripts.seed --reset

# test
pytest

# lint
ruff check .
ruff format .

# build Lambda zip
./scripts/build-zip.sh
```

## Environment

12 env vars via `.env` or Pydantic Settings:
- `AUTH0_DOMAIN`, `AUTH0_AUDIENCE`
- `DB_BACKEND` (`sqlite` | `dynamo`), `SQLITE_PATH`, `DYNAMODB_TABLE_PREFIX`, `AWS_REGION`
- `CORS_ORIGINS`, `DEBUG`, `DEV_USER`

DynamoDB tables: `lingo_users`, `lingo_srs`, `lingo_decks` w/ GSIs — see README for `aws dynamodb create-table` commands.

## Cost telemetry

Spend tagging happens at two levels:

1. **Per-table cost allocation tags** — `Project`, `Environment`, `Domain`
   tags are applied to every `aws_dynamodb_table` in `lingo-infra/main.tf`.
   `lingo-ops` then queries AWS Cost Explorer grouped by those tags and
   exposes `/api/ops/v1/finance/costs/by-domain`. See
   `lingo-infra/docs/cost-tags.md` for the tag set + the one-time AWS
   Billing console activation step.
2. **Per-callsite structured logs** — `app/db/dynamo/telemetry.py`
   exposes `log_dynamo_op(table, operation, callsite)` which emits one
   JSON line to the `lingo.dynamo` logger. CloudWatch Logs Insights then
   answers "which router function is hammering which table?" — a
   question AWS billing alone can't answer because tags don't attach to
   individual API calls.

**When you touch any Dynamo code path,** call `log_dynamo_op` once per
boto3 op with the dotted callsite (e.g. `"social.router.list_friends"`).
The helper is intentionally **not** wired everywhere yet — that's a
large refactor — but every new Dynamo callsite SHOULD adopt it and any
edit to an existing callsite is a good moment to add the line. Example:

```python
from app.db.dynamo.telemetry import log_dynamo_op

log_dynamo_op(
    table="lingo_social",
    operation="Query",
    callsite="social.router.list_friends",
)
await self._table.query(...)
```

CloudWatch Insights query example:

```
fields @timestamp, table, op, callsite | stats count() by table, callsite
```

## Don't

- **Don't use `datetime.utcnow()`** — deprecated in py3.13.
- **Don't import a concrete repo** in a router — go through `provider.get_*_repository()`.
- **Don't add `setup.py`** — pyproject.toml only.
- **Don't put dev deps outside `[project.optional-dependencies] dev`**.
- **Don't add legacy compat shims** when changing routes/endpoints.
- **Don't add AI attribution to commits.**
- **Don't trust admin routes are gated** — they aren't, until role enforcement lands.
