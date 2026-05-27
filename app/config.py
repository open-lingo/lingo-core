import os
from pathlib import Path

from pydantic_settings import BaseSettings

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    # Auth0
    AUTH0_DOMAIN: str = ""
    AUTH0_AUDIENCE: str = ""
    AUTH0_ALGORITHMS: list[str] = ["RS256"]

    # Database backend: "sqlite" for local dev, "dynamodb" for prod
    DB_BACKEND: str = "sqlite"
    SQLITE_PATH: str = "local.db"

    # DynamoDB (only needed when DB_BACKEND=dynamodb)
    DYNAMODB_TABLE_PREFIX: str = "lingo_"
    AWS_REGION: str = "us-east-1"

    # CORS — dev defaults include Vite's strict port and common loopback variants.
    CORS_ORIGINS: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]

    DEBUG: bool = False

    # Dev-mode default identity (used by seed script and auth bypass).
    # When DEBUG=true and no X-Dev-User header / Bearer token is sent,
    # requests authenticate as this user automatically.
    DEV_USER: str = "dev|user-1"

    # Fix 4 — admin allow-list. Until OAuth scopes land, admin routes are
    # gated by membership in this set. Populated via ``ADMIN_USER_IDS`` in
    # .env as a JSON list. Either internal UUIDs *or* Auth0 subs are accepted.
    ADMIN_USER_IDS: list[str] = []

    # Funding transparency meter (public GET /finance/transparency)
    FUNDING_AD_PERCENT: int = 40
    FUNDING_PERIOD_LABEL: str = "Last 30 days"
    # manual | estimated | live (live = future AdSense+Stripe snapshot job)
    FUNDING_SOURCE: str = "estimated"

    model_config = {"env_file": str(_PROJECT_ROOT / ".env"), "env_file_encoding": "utf-8"}

    @property
    def funding_ad_percent(self) -> int:
        return max(0, min(100, self.FUNDING_AD_PERCENT))

    @property
    def funding_period_label(self) -> str:
        return self.FUNDING_PERIOD_LABEL

    @property
    def funding_source(self) -> str:
        s = (self.FUNDING_SOURCE or "estimated").lower()
        return s if s in ("manual", "estimated", "live") else "estimated"


def _guard_debug_in_prod(s: "Settings") -> None:
    """Fix 5 — refuse to boot with DEBUG=true in a real Lambda environment.

    AWS Lambda always sets ``AWS_LAMBDA_FUNCTION_NAME``; CI / dev / docker
    do not. We treat the presence of that env var as a hard signal that we
    are running in production.
    """
    if not s.DEBUG:
        return
    if os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
        raise RuntimeError("DEBUG=true is not allowed in production environments (AWS_LAMBDA_FUNCTION_NAME is set).")


# Wrap Settings.__init__ so direct ``Settings()`` calls (e.g. in tests) also
# raise. The module-level singleton below uses the same path.
_orig_init = Settings.__init__


def _patched_init(self: "Settings", *args: object, **kwargs: object) -> None:
    _orig_init(self, *args, **kwargs)  # type: ignore[arg-type]
    _guard_debug_in_prod(self)


Settings.__init__ = _patched_init  # type: ignore[assignment]

settings = Settings()

if not Path(settings.SQLITE_PATH).is_absolute():
    settings.SQLITE_PATH = str(_PROJECT_ROOT / settings.SQLITE_PATH)
