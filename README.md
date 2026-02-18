# lingo-core

Core API for Open Lingo — user management, progress tracking, course library manifest tracking, addon manifest tracking.

## Stack

- **FastAPI** + **uvicorn**
- **Auth0** JWT validation (RS256)
- **DynamoDB** (prod) / **SQLite** (local dev) — swapped via DI
- **Pydantic Settings** for env-driven config

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env    # edit Auth0 values
uvicorn app.main:app --reload
```

The API starts on `http://localhost:8000`. Docs at `/docs`.

## Project structure

```
app/
├── main.py              # App factory, lifespan, CORS
├── config.py            # Pydantic Settings (reads .env)
├── auth/
│   ├── dependencies.py  # get_current_user (Auth0 JWT)
│   └── schemas.py       # TokenPayload
├── db/
│   ├── protocols.py     # Repository interfaces (Protocol classes)
│   ├── sqlite.py        # SQLite impl (local dev)
│   ├── dynamo.py        # DynamoDB impl (prod)
│   └── dependencies.py  # DI wiring — init + get_*_repo
└── users/
    ├── router.py        # /api/core/users/v1/*
    └── schemas.py       # UserSettings, UserProfile
```

## Database DI

Set `DB_BACKEND` in `.env`:

- `sqlite` — data stored in `local.db`, auto-creates tables on startup
- `dynamodb` — single-table design, requires AWS creds + table

Both backends implement the same `UserRepository` protocol so calling code is identical.

## API routes

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/core/users/v1/me/settings` | Bearer | Get user settings |
| PATCH | `/api/core/users/v1/me/settings` | Bearer | Merge-update settings |
| GET | `/health` | — | Health check |
