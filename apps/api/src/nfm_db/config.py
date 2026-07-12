"""Application configuration via environment variables."""

import os

from pydantic_settings import BaseSettings

# ---------------------------------------------------------------------------
# Version lock — must match docker/lightrag.Dockerfile ARG LIGHTRAG_VERSION
# ---------------------------------------------------------------------------
LIGHTRAG_VERSION: str = os.environ.get("LIGHTRAG_VERSION", "1.5.4")


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    database_url: str = "postgresql+asyncpg://nfm:nfm@localhost:5432/nfm_db"
    debug: bool = True
    cors_origins: list[str] = ["http://localhost:3000"]
    secret_key: str = "CHANGE_THIS_IN_PRODUCTION"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    blog_content_dir: str = "content/blog"
    lightrag_host: str = "localhost"
    lightrag_port: int = 9621
    lightrag_version: str = LIGHTRAG_VERSION

    model_config = {"env_file": ".env", "env_prefix": "NFM_"}


def get_settings() -> Settings:
    """Return cached application settings."""
    return Settings()
