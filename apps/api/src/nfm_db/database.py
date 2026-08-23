"""Database engine and session management."""

import logging
from collections.abc import AsyncGenerator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# NFM-3522: register the after_commit listener at import time.
import nfm_db.services.lightrag_dispatcher  # noqa: F401
from nfm_db.config import get_settings

logger = logging.getLogger(__name__)

engine = create_async_engine(
    get_settings().database_url,
    echo=get_settings().debug,
)


@event.listens_for(engine.sync_engine, "connect")
def _load_age_extension(dbapi_conn: object, connection_record: object) -> None:
    """Load Apache AGE extension on PostgreSQL connections.

    This sets search_path so AGE graph functions are available
    alongside normal relational queries.  No-op on non-PostgreSQL
    backends (e.g. SQLite for tests).
    """
    cursor = getattr(dbapi_conn, "cursor", None)
    if cursor is None:
        return
    # Detect PostgreSQL via the connection's dialect info
    try:
        cursor.execute("SELECT current_database()")
        cursor.execute("LOAD 'age';")
        cursor.execute('SET search_path TO ag_catalog, "$current_schema";')
    except Exception:
        logger.warning("AGE extension not available, skipping LOAD 'age' setup", exc_info=True)


async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Yield an async database session."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
