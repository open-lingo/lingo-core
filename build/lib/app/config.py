from pydantic_settings import BaseSettings


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

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
