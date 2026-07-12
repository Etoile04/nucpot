"""Database engine and session management."""

from collections.abc import AsyncGenerator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from nfm_db.config import get_settings

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
        # Non-PostgreSQL or AGE not installed — skip silently
        pass


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
