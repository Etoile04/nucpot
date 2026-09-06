"""Database engine and session management (session-provider).

ADR-NFM-4076: this module is the single seam deciding who gets a database
session, when, and under which pool policy.  Engine construction is lazy —
importing this module builds no engine; the first session use does.
"""

import logging
import threading
import uuid
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any, Protocol

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool, Pool

from nfm_db.config import get_settings

logger = logging.getLogger(__name__)

# parse 失败标记常量。截断长度 500 对齐 literature_service._mark_failed_async
# 的现状(str(err)[:500]);注意与该文件另一路径的 MAX_ERROR_LEN(1000)不同值。
# T2 收编失败标记时把两边统一到单一出处。
_PARSE_FAILED_STATUS = "failed"
_PARSE_ERROR_MAX_LEN = 500

# 收编前的兼容类型:async_sessionmaker 满足它;测试的鸭子类型工厂也满足它。
# T2+ 迁移完成后评估是否收窄为 async_sessionmaker。
class _SessionFactory(Protocol):
    def __call__(self) -> AbstractAsyncContextManager[AsyncSession]: ...

_engine: AsyncEngine | None = None
_default_factory: async_sessionmaker[AsyncSession] | None = None
_engine_lock = threading.Lock()


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


def _new_engine(poolclass: type[Pool] | None = None) -> AsyncEngine:
    """Build a fresh engine with the AGE connect listener attached.

    Never cached here — the shared engine is cached by :func:`get_engine`;
    task-scoped engines are built and disposed per task.
    """
    kwargs: dict[str, Any] = {"echo": get_settings().debug}
    if poolclass is not None:
        kwargs["poolclass"] = poolclass
    engine = create_async_engine(get_settings().database_url, **kwargs)
    event.listens_for(engine.sync_engine, "connect")(_load_age_extension)
    return engine


def get_engine() -> AsyncEngine:
    """Return the shared engine, building it on first use (ADR-NFM-4076 D2).

    Importing this module must not construct an engine: the historical
    import-time build leaked a pool object into every importer, including
    Celery prefork parents, which is the BUG-22 (NFM-4076) exposure class.
    """
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                _engine = _new_engine()
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the default (request/shared) session factory, lazily built.

    Builds the engine inline while holding ``_engine_lock`` — calling
    :func:`get_engine` here would re-acquire the non-reentrant lock from
    the same thread and deadlock (observed as a hung mapper test).
    """
    global _default_factory, _engine
    if _default_factory is None:
        with _engine_lock:
            if _default_factory is None:
                if _engine is None:
                    _engine = _new_engine()
                _default_factory = async_sessionmaker(
                    _engine, class_=AsyncSession, expire_on_commit=False
                )
    return _default_factory


def reset_for_tests() -> None:
    """Drop cached engine/factory so the next use rebuilds from settings.

    Test-isolation hook only (same family as flag-service's
    ``__resetFlagCacheForTests``): production code must never call it.
    Needed because a provider built under one test's env (e.g. an
    in-memory SQLite ``NFM_DATABASE_URL``) must not leak into later
    tests that expect a fresh resolve.
    """
    global _engine, _default_factory
    with _engine_lock:
        _engine = None
        _default_factory = None


class _LazyDefaultSessionFactory:
    """Compat alias for the historical ``async_session_factory`` attribute.

    Callers (and tests) still do ``async_session_factory()``; the proxy
    defers engine construction to the first call.  T2/T4 migrate call
    sites onto :func:`get_session_factory` / :func:`task_session_factory`;
    T5 removes this alias once no caller remains (ADR-NFM-4076 D2/D5).

    Deliberately defines ONLY ``__call__``: no ``__getattr__`` forwarding —
    ``unittest.mock.patch`` inspects the original attribute value, and a
    forwarding ``__getattr__`` would route that inspection through the
    engine-building lock path (the same-thread lock re-entry that once
    hung the mapper tests).
    """

    def __call__(self, *args: Any, **kwargs: Any) -> AsyncSession:
        return get_session_factory()(*args, **kwargs)


async_session_factory = _LazyDefaultSessionFactory()


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Yield an async database session (the FastAPI adapter).

    Resolves the default factory through the module attribute at call
    time, mirroring the historical wiring: existing tests that replace
    ``async_session_factory`` keep working unchanged until they migrate
    onto ``dependency_overrides`` / :func:`get_session_factory` (T2+).
    """
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def task_session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Yield a task-local session factory on a fresh NullPool engine.

    BUG-22 (NFM-4076): the shared engine's asyncpg pool binds its
    connections to the event loop that first used them, so Celery prefork
    workers running each task through a fresh ``asyncio.run`` loop fail
    with ``Future attached to a different loop``.  This adapter gives every
    task its own engine bound to the task's loop and disposes it on exit,
    so no pool state crosses the task boundary (ADR-NFM-4076 D3).

    The AGE extension listener is attached like on the shared engine so
    graph functions resolve identically in and out of tasks.
    """
    engine = _new_engine(poolclass=NullPool)
    try:
        yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    finally:
        await engine.dispose()


async def mark_parse_failed(
    datasource_id: uuid.UUID | str,
    err: BaseException,
    *,
    session_factory: _SessionFactory | None = None,
) -> None:
    """Best-effort failure mark on a DataSource row (CONTEXT.md: parse 失败标记).

    Called from the extraction pipeline's outermost ``except`` so a crash
    can never leave a row spinning in ``parsing``.  This function must
    never raise — a failed mark is logged, never allowed to mask the
    original exception that triggered it.

    Without ``session_factory`` the mark runs through the task-scoped
    adapter (its own NullPool engine), so it works even when the caller's
    own engine/session is the thing that just failed.
    """
    try:
        ds_id: uuid.UUID | str = datasource_id
        if isinstance(ds_id, str):
            try:
                ds_id = uuid.UUID(ds_id)
            except ValueError:
                pass
        if session_factory is not None:
            await _write_parse_failed(session_factory, ds_id, err)
        else:
            async with task_session_factory() as task_factory:
                await _write_parse_failed(task_factory, ds_id, err)
    except Exception:
        logger.exception("Could not mark datasource %s parse-failed", datasource_id)


async def _write_parse_failed(
    factory: _SessionFactory,
    ds_id: uuid.UUID | str,
    err: BaseException,
) -> None:
    from nfm_db.models.source import DataSource  # 函数体内 import:避免模型↔database 循环

    async with factory() as session:
        row = await session.get(DataSource, ds_id)
        if row is None:
            logger.warning("parse-failure mark: datasource %s not found", ds_id)
            return
        # updated_at 由 TimestampMixin.onupdate 自动维护,不显式赋值。
        row.parse_status = _PARSE_FAILED_STATUS
        row.parse_error = str(err)[:_PARSE_ERROR_MAX_LEN]
        await session.commit()


@asynccontextmanager
async def fresh_session() -> AsyncIterator[AsyncSession]:
    """Yield a brand-new session from the default factory (ADR-NFM-4076 D4).

    The poisoned-session recovery paths (NFM-3322) must never trust the
    session that just failed them: this channel hands out a fresh session
    from the default factory with no auto-commit — the recovery path
    controls its own transaction boundary.
    """
    async with get_session_factory()() as session:
        yield session
