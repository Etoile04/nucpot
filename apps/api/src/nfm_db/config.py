"""Application configuration via environment variables."""

import os

from pydantic_settings import BaseSettings

# ---------------------------------------------------------------------------
# Version lock — must match docker/lightrag.Dockerfile ARG LIGHTRAG_VERSION
# ---------------------------------------------------------------------------
LIGHTRAG_VERSION: str = os.environ.get("LIGHTRAG_VERSION", "1.5.4")


def _resolve_service_jwt_ttl() -> int:
    """Resolve the service-account JWT TTL from environment.

    Reads ``NUCPOT_SERVICE_JWT_TTL_MINUTES`` (NFM-1973 / NFM-1972 AC-1
    contract) and falls back to the Pydantic default if the variable is
    unset or unparseable.  We do not route this through the standard
    ``NFM_`` prefix because the AC explicitly named the env var.
    """
    raw = os.environ.get("NUCPOT_SERVICE_JWT_TTL_MINUTES")
    if raw is None or not raw.strip():
        return 30
    try:
        value = int(raw)
    except ValueError:
        return 30
    if value <= 0:
        return 30
    return value


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    database_url: str = "postgresql+asyncpg://nfm:nfm@localhost:5432/nfm_db"
    debug: bool = True
    cors_origins: list[str] = ["http://localhost:3000"]
    secret_key: str = "CHANGE_THIS_IN_PRODUCTION"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    # NFM-1973 / NFM-1972 AC-1: TTL for service-account JWTs.
    # Configurable via NUCPOT_SERVICE_JWT_TTL_MINUTES env var, default 30 minutes.
    # Independent of human-user TTL so operators can tighten/loosen service
    # tokens without affecting interactive sessions.
    service_jwt_ttl_minutes: int = _resolve_service_jwt_ttl()
    blog_content_dir: str = "content/blog"
    lightrag_host: str = "localhost"
    lightrag_port: int = 9621
    lightrag_version: str = LIGHTRAG_VERSION

    model_config = {"env_file": ".env", "env_prefix": "NFM_", "extra": "ignore"}


def get_settings() -> Settings:
    """Return cached application settings."""
    return Settings()
