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

    # CORS
    CORS_ORIGINS: list[str] = ["http://localhost:5173"]

    DEBUG: bool = False

    # Dev-mode default identity (used by seed script and auth bypass).
    # When DEBUG=true and no X-Dev-User header / Bearer token is sent,
    # requests authenticate as this user automatically.
    DEV_USER: str = "dev|user-1"

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


settings = Settings()

if not Path(settings.SQLITE_PATH).is_absolute():
    settings.SQLITE_PATH = str(_PROJECT_ROOT / settings.SQLITE_PATH)
