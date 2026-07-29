"""Dependency injection and settings for the NFM MCP server."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


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

    # Database connection
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


# ── Engine lifecycle (module-level singleton) ──────────────────

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _get_engine() -> AsyncEngine:
    """Lazily create (and cache) the async SQLAlchemy engine."""
    global _engine, _session_factory
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            settings.database_url,
            pool_size=settings.database_pool_size,
            pool_timeout=settings.database_pool_timeout,
            echo=False,
        )
        _session_factory = async_sessionmaker(
            _engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        logger.info("Created async engine for %s", settings.database_url)
    return _engine


def _get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the cached session factory, creating the engine if needed."""
    _get_engine()
    assert _session_factory is not None
    return _session_factory


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield an async database session with automatic cleanup.

    Creates a session from the shared engine, yields it for use in
    tool handlers, and handles commit/rollback/close in a context
    manager.  The caller does **not** need to commit — the service
    layer already handles commits for write operations.
    """
    factory = _get_session_factory()
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
