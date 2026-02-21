# Open Lingo — Backend (lingo-core)

FastAPI backend for the Open Lingo language learning platform.

## Stack

| Concern | Choice |
|---|---|
| Framework | FastAPI 0.115+ |
| Language | Python 3.13+ |
| Server | Uvicorn |
| Auth | Auth0 RS256 JWT (`python-jose`) |
| Local DB | SQLite (`aiosqlite`) |
| Cloud DB | AWS DynamoDB (`aioboto3`) |
| Config | Pydantic Settings 2 (`.env`) |
| Linting | Ruff |
| Testing | pytest + pytest-asyncio |

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env    # fill in Auth0 values
uvicorn app.main:app --reload
```

API: `http://localhost:8000`  
Swagger docs: `http://localhost:8000/docs`

### Environment variables

```
# Auth
AUTH0_DOMAIN=your-tenant.auth0.com
AUTH0_AUDIENCE=...

# Database — "sqlite" (local) or "dynamodb" (prod)
DB_BACKEND=sqlite
SQLITE_PATH=./lingo.db

# Dev only — bypasses JWT validation
DEBUG=false
DEV_USER=dev|user-1       # identity used when DEBUG=true

# CORS — JSON array of allowed origins
CORS_ORIGINS=["http://localhost:5173"]
```

## Scripts

```bash
# Start dev server
uvicorn app.main:app --reload

# Seed the local SQLite database
python -m scripts.seed            # skip existing rows
python -m scripts.seed --reset    # wipe and re-seed

# Lint
ruff check .

# Tests
pytest
```

## Project structure

```
app/
├── main.py               # FastAPI app factory, lifespan, CORS, access-log middleware
├── config.py             # Pydantic Settings — all env vars
├── auth/
│   ├── dependencies.py   # get_current_user, get_current_user_optional; debug bypass
│   └── schemas.py        # TokenPayload
├── db/
│   ├── protocols.py      # Repository Protocol interfaces
│   ├── sqlite.py         # SqliteUserRepository
│   ├── srs_sqlite.py     # SqliteSRSRepository
│   ├── deck_sqlite.py    # SqliteDeckRepository
│   ├── subscription_sqlite.py   # SqliteSubscriptionRepository
│   ├── dynamo.py         # DynamoUserRepository (prod)
│   ├── mock_community.py # In-memory community repo (active)
│   └── dependencies.py   # DI wiring — init_repositories, get_*_repo
├── users/
│   ├── router.py         # /api/core/users/v1/*
│   ├── schemas.py
│   └── subscriptions/    # Content-type handlers (deck, addon, story)
├── srs/
│   ├── router.py         # /api/core/srs/v1/*
│   └── schemas.py
├── decks/
│   ├── router.py         # /api/core/decks/v1/*
│   └── schemas.py
└── community/
    ├── router.py         # /api/core/community/v1/*
    └── schemas.py
```

## API reference

### Health

| Method | Path | Auth |
|---|---|---|
| GET | `/health` | None |

### Users — `/api/core/users/v1`

| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/me` | Bearer | Register / upsert on first login |
| GET | `/me` | Bearer | Get current user |
| PATCH | `/me` | Bearer | Update profile |
| GET | `/u/{username}` | None | Public profile lookup |
| GET | `/me/settings` | Bearer | Get user settings |
| PATCH | `/me/settings` | Bearer | Merge-patch settings |
| GET | `/me/subscriptions` | Bearer | List subscriptions (`?content_type=deck`) |
| POST | `/me/subscriptions` | Bearer | Add subscription |
| PATCH | `/me/subscriptions/{type}/{id}` | Bearer | Update subscription settings |
| DELETE | `/me/subscriptions/{type}/{id}` | Bearer | Remove subscription |

Subscription settings: `enabled`, `newCardsPerDay`, `newCardOrder` (`ordered` \| `shuffled`).

### SRS — `/api/core/srs/v1`

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/state` | Bearer | Full SRS state map for the user |
| GET | `/due` | Bearer | Cards due on or before `?on_or_before=YYYY-MM-DD` |
| POST | `/sync` | Bearer | Delta sync — last-write-wins by `lastReviewDate`. Returns merged state. |
| DELETE | `/cards` | Bearer | Delete specific card states (body: `{cardIds: [...]}`) |
| DELETE | `/all` | Bearer | Wipe all SRS state for the user |

### Decks — `/api/core/decks/v1`

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/decks` | Bearer | List user's own decks |
| POST | `/decks` | Bearer | Create deck (starts as `draft`) |
| GET | `/decks/batch` | Bearer | Batch fetch by `?ids=id1,id2,...` |
| GET | `/decks/admin` | Bearer | List all decks for admin review |
| PATCH | `/decks/admin/{id}/status` | Bearer | Approve (`published`) or reject (`draft`) |
| GET | `/decks/{id}` | Bearer | Get deck (must own if draft) |
| PUT | `/decks/{id}` | Bearer | Replace deck content (author only) |
| PATCH | `/decks/{id}/status` | Bearer | Change status (author only) |

### Community — `/api/core/community/v1`

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/categories` | None | List forum categories |
| GET | `/tags` | None | List tags |
| POST | `/tags` | Optional | Create tag |
| GET | `/threads` | None | List threads (filter: `category`, `tag`, `content`, `sort`) |
| POST | `/threads` | Bearer | Create thread |
| GET | `/threads/{id}` | None | Get thread (increments views) |
| PATCH | `/threads/{id}` | Bearer | Update thread |
| POST/DELETE | `/threads/{id}/vote` | Bearer | Vote / remove vote |
| GET | `/threads/{id}/posts` | None | List replies |
| POST | `/threads/{id}/posts` | Bearer | Create reply |
| PATCH | `/posts/{id}` | Bearer | Update post |
| POST | `/posts/{id}/vote` | Bearer | Vote on post |
| GET | `/content/{type}/{id}/threads` | None | Threads linked to a piece of content |
| GET | `/addons` | None | List addons |
| POST | `/addons` | Bearer | Create addon |
| GET/PATCH | `/addons/{id}` | None/Bearer | Get or update addon |
| PUT | `/addons/{id}/deck` | Bearer | Store flashcard pack content |
| GET | `/addons/{id}/deck` | None | Get flashcard pack content |
| PUT/GET/DELETE | `/markdown` / `/markdown/{key}` | Bearer/None | Markdown storage by key |

## Authentication

**Production:** Auth0 RS256 JWT. The JWKS is fetched and cached from Auth0 on startup. Every protected request validates the Bearer token against the JWKS.

**Local dev (`DEBUG=true`):** JWT validation is skipped. Identity is read from the `X-Dev-User` header, or falls back to the `DEV_USER` env var. **Never set `DEBUG=true` in production.**

Two dependency variants:
- `get_current_user` — requires valid auth, returns 401 otherwise
- `get_current_user_optional` — returns `None` if no valid auth (used for public-read endpoints)

## Database

A **repository pattern** decouples all data access. Each repo implements a `Protocol` interface; the rest of the app never touches the DB directly.

### SQLite (local dev)

`DB_BACKEND=sqlite` — uses `aiosqlite` for async access. Schema is created automatically on startup.

Tables:

| Table | Description |
|---|---|
| `users` | User records (`auth0_id`, `username`, `email`, ...) |
| `user_settings` | Per-user settings JSON blob |
| `srs_cards` | Per-card SRS state (`auth0_id`, `card_id`, SM-2 fields, `buriedUntil`) |
| `deck_manifests` | Deck metadata (`id`, `name`, `status`, `author`, ...) |
| `deck_content` | Deck card content (JSON) |
| `subscriptions` | User content subscriptions (`auth0_id`, `content_type`, `content_id`, settings) |

### DynamoDB (production)

`DB_BACKEND=dynamodb` — uses `aioboto3`. `DynamoUserRepository` is implemented. SRS and deck DynamoDB repos are not yet implemented (raise `NotImplementedError`).

### Community

Always uses an **in-memory mock repository** regardless of `DB_BACKEND`. Persistent SQLite/DynamoDB implementations exist as stubs and are not yet wired up. Community data is reset on server restart.
