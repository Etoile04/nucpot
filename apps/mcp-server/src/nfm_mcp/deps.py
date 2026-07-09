"""Dependency injection and settings for the NFM MCP server."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Server configuration loaded from environment variables.

    All settings use the ``NFM_MCP_`` prefix and can be overridden via
    a ``.env`` file placed next to the entry-point.
    """

    model_config = SettingsConfigDict(
        env_prefix="NFM_MCP_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database connection (Phase B will use these)
    database_url: str = "postgresql+asyncpg://nfm:nfm@localhost:5432/nfm"
    database_pool_size: int = 5
    database_pool_timeout: float = 30.0

    # NFM REST API base URL (fallback when direct DB is unavailable)
    api_base_url: str = "http://localhost:8000/v1"
    api_timeout: float = 30.0

    # Knowledge-graph service URL
    kg_service_url: str = "http://localhost:8001"

    # Transport selection
    transport: str = "stdio"
    host: str = "127.0.0.1"
    port: int = 8002

    # Logging
    log_level: str = "INFO"

    # LLM service (for extraction pipeline)
    llm_service_url: str = "http://localhost:8003"


def get_settings() -> Settings:
    """Return a cached ``Settings`` singleton."""
    return Settings()


async def get_db_session() -> AsyncGenerator[Any, None]:
    """Yield an async database session.

    **Phase A (stub):** yields ``None``.
    **Phase B:** will swap to a real SQLAlchemy async session factory.
    """
    yield None
