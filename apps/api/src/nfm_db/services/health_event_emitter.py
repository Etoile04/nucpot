"""Health event emitter for structured silent-failure tracking (NFM-2220).

Replaces bare ``except: pass`` blocks so that a swallowed failure always
leaves an observable trace.

Two entry points are provided:

``emit_health_event()``
    Async, and the **preferred** form. Use it from every ``async def``
    call site so the insert is awaited to completion.

``emit_health_event_sync()``
    For call sites that are genuinely synchronous (SSH cleanup, SFTP
    helpers, per-record mapper helpers). It hands the insert to a
    dedicated background event loop and *waits* for the result, so the
    row is durable before the function returns.

Both are **best effort and never raise**. A health event must never
become the reason a request fails — if the insert cannot be done the
event is written to the log instead, so the information is still
recoverable.

Why the sync form does not use ``loop.create_task`` (NFM-2241 C1)
----------------------------------------------------------------
Scheduling a fire-and-forget task on the *caller's* loop loses the event
whenever that loop is torn down before the task runs. The Celery bridge
:func:`nfm_db.services.literature_service.process_literature_sync` calls
``asyncio.run(...)``, which cancels all pending tasks at teardown, so a
scheduled insert reached neither the table nor the log. The emitter
therefore owns a long-lived loop whose lifetime is independent of any
request, and the sync entry point blocks briefly on that loop.

The emitter deliberately opens its **own** session rather than reusing
the caller's. Several call sites emit precisely because the caller's
session has just failed (e.g. a rollback error), so reusing it would
lose the event.

Usage::

    from nfm_db.services.health_event_emitter import (
        EVENT_FALLBACK_TRIGGERED,
        SEVERITY_WARNING,
        build_context,
        emit_health_event,
    )

    try:
        risky_operation()
    except Exception as exc:
        await emit_health_event(
            event_type=EVENT_FALLBACK_TRIGGERED,
            severity=SEVERITY_WARNING,
            source_service="mineru_extraction",
            context=build_context(exc),
        )
"""

from __future__ import annotations

import asyncio
import atexit
import logging
import threading
import uuid
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import Table as SATable
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from nfm_db.database import async_session_factory
from nfm_db.models.health_event import HealthEvent

logger = logging.getLogger(__name__)

# --- Vocabulary -------------------------------------------------------------

# Severity values accepted by the ``health_events.severity`` column.
SEVERITY_INFO = "info"
SEVERITY_WARNING = "warning"
SEVERITY_ERROR = "error"
SEVERITY_CRITICAL = "critical"

VALID_SEVERITIES = frozenset(
    {SEVERITY_INFO, SEVERITY_WARNING, SEVERITY_ERROR, SEVERITY_CRITICAL}
)

#: The five ``event_type`` values enumerated by the NFM-2211-B spec. The
#: ``GET /api/v1/health/alerts`` endpoint (NFM-2211-C) filters on these, so
#: an unrecognised value would silently create a category no alert query
#: looks at. Unknown values are coerced to
#: :data:`EVENT_GENERIC_SILENT_CATCH` and logged (NFM-2241 M2/M3).
EVENT_FALLBACK_TRIGGERED = "fallback_triggered"
EVENT_VALIDATION_DROP = "validation_drop"
EVENT_CATEGORY_COERCION_FAIL = "category_coercion_fail"
EVENT_ASYNCIO_CRASH = "asyncio_crash"
EVENT_GENERIC_SILENT_CATCH = "generic_silent_catch"

VALID_EVENT_TYPES = frozenset(
    {
        EVENT_FALLBACK_TRIGGERED,
        EVENT_VALIDATION_DROP,
        EVENT_CATEGORY_COERCION_FAIL,
        EVENT_ASYNCIO_CRASH,
        EVENT_GENERIC_SILENT_CATCH,
    }
)

#: How long :func:`emit_health_event_sync` waits for the background insert.
#: Generous enough for a healthy database, short enough that a wedged one
#: cannot stall a cleanup path.
SYNC_EMIT_TIMEOUT_SECONDS = 5.0


def build_context(exc: BaseException | None = None, **fields: Any) -> dict[str, Any]:
    """Build a JSONB-safe context payload.

    Adds ``timestamp`` plus, when *exc* is supplied, ``error`` and
    ``exception_type``. Extra keyword fields are merged in and win over
    the derived ones.
    """
    context: dict[str, Any] = {"timestamp": datetime.now(UTC).isoformat()}
    if exc is not None:
        context["error"] = str(exc)
        context["exception_type"] = type(exc).__name__
    context.update(fields)
    return context


def _prepare(
    severity: str, source_service: str, event_type: str, context: dict[str, Any] | None
) -> tuple[str, str, dict[str, Any]]:
    """Copy and stamp the context, then validate severity and event type.

    The context is copied rather than mutated so the caller's dict does
    not gain a ``timestamp`` key as a side effect.

    Returns:
        ``(event_type, severity, payload)`` with both enum fields coerced
        into the vocabularies the alert queries understand.
    """
    payload: dict[str, Any] = dict(context) if context else {}
    payload.setdefault("timestamp", datetime.now(UTC).isoformat())

    if severity not in VALID_SEVERITIES:
        logger.warning(
            "Health event used unknown severity %r (coercing to %r): %s/%s",
            severity,
            SEVERITY_ERROR,
            source_service,
            event_type,
        )
        severity = SEVERITY_ERROR

    if event_type not in VALID_EVENT_TYPES:
        # Keep the caller's label in the payload — the original name is
        # usually the most useful field when triaging the alert.
        logger.warning(
            "Health event used unknown event_type %r (coercing to %r): %s",
            event_type,
            EVENT_GENERIC_SILENT_CATCH,
            source_service,
        )
        payload.setdefault("reported_event_type", event_type)
        event_type = EVENT_GENERIC_SILENT_CATCH

    return event_type, severity, payload


async def emit_health_event(
    event_type: str,
    severity: str,
    source_service: str,
    context: dict[str, Any] | None = None,
    session_factory: async_sessionmaker[Any] | None = None,
) -> bool:
    """Record a structured health event. Never raises.

    Uses a SQLAlchemy Core ``INSERT`` rather than the ORM unit of work:
    several call sites emit once per record, so the flush and
    identity-map overhead of ``session.add`` is not worth paying
    (NFM-2241 H2).

    Args:
        event_type: One of :data:`VALID_EVENT_TYPES`. Anything else is
            coerced to ``generic_silent_catch``.
        severity: One of :data:`VALID_SEVERITIES`.
        source_service: Service that caught the exception, e.g.
            ``"mineru_extraction"``.
        context: Structured metadata stored in the JSONB column.
        session_factory: Override for the session source. Defaults to the
            application factory; :func:`emit_health_event_sync` passes a
            factory bound to the emitter's own event loop.

    Returns:
        ``True`` if the row was committed, ``False`` if the emitter fell
        back to logging.
    """
    event_type, severity, payload = _prepare(
        severity, source_service, event_type, context
    )
    factory = session_factory if session_factory is not None else async_session_factory

    try:
        async with factory() as session:
            await session.execute(
                insert(cast(SATable, HealthEvent.__table__)).values(
                    id=uuid.uuid4(),
                    event_type=event_type,
                    severity=severity,
                    source_service=source_service,
                    context=payload,
                )
            )
            await session.commit()
    except Exception as exc:
        # The health event itself is never swallowed — if the database is
        # unreachable the payload still reaches the log.
        logger.warning(
            "Health event DB insert failed (logging fallback): "
            "%s/%s [%s] context=%s reason=%s",
            source_service,
            event_type,
            severity,
            payload,
            exc,
        )
        return False

    logger.debug(
        "Health event recorded: %s/%s [%s]", source_service, event_type, severity
    )
    return True


# ---------------------------------------------------------------------------
# Dedicated emitter loop backing ``emit_health_event_sync``
# ---------------------------------------------------------------------------
# One daemon thread runs a loop for the lifetime of the process. Its engine
# is created *on that loop*, because asyncpg connections are bound to the
# loop that opened them and must never be handed to a different one.

_loop_lock = threading.Lock()
_emitter_loop: asyncio.AbstractEventLoop | None = None
_emitter_session_factory: async_sessionmaker[Any] | None = None
_emitter_stopped = False


def _run_emitter_loop(loop: asyncio.AbstractEventLoop) -> None:
    asyncio.set_event_loop(loop)
    loop.run_forever()


def _get_emitter_loop() -> asyncio.AbstractEventLoop | None:
    """Return the emitter loop, starting it on first use.

    Returns ``None`` once the process has begun shutting down, so a late
    emit falls back to logging instead of resurrecting a dead thread.
    """
    global _emitter_loop
    with _loop_lock:
        if _emitter_stopped:
            return None
        if _emitter_loop is not None and not _emitter_loop.is_closed():
            return _emitter_loop
        loop = asyncio.new_event_loop()
        threading.Thread(
            target=_run_emitter_loop,
            args=(loop,),
            name="health-event-emitter",
            daemon=True,
        ).start()
        _emitter_loop = loop
        return loop


async def _get_emitter_session_factory() -> async_sessionmaker[Any]:
    """Build (once) a session factory owned by the emitter loop.

    Runs *inside* the emitter loop so the engine's connections belong to
    it. The pool is deliberately tiny: this path carries failure
    telemetry only, never user traffic.
    """
    global _emitter_session_factory
    if _emitter_session_factory is None:
        from nfm_db.config import get_settings

        engine = create_async_engine(
            get_settings().database_url,
            pool_size=1,
            max_overflow=1,
            pool_pre_ping=True,
            echo=False,
        )
        _emitter_session_factory = async_sessionmaker(engine, expire_on_commit=False)
    return _emitter_session_factory


async def _emit_on_emitter_loop(
    event_type: str,
    severity: str,
    source_service: str,
    context: dict[str, Any],
) -> bool:
    try:
        factory = await _get_emitter_session_factory()
        return await emit_health_event(
            event_type=event_type,
            severity=severity,
            source_service=source_service,
            context=context,
            session_factory=factory,
        )
    except asyncio.CancelledError:
        # NFM-2241 C3: surface the asyncio crash on the telemetry stream
        # rather than letting the emitter swallow it. The original event
        # payload stays in the log so the failure is still traceable.
        logger.warning(
            "Health event asyncio.CancelledError on emitter loop: "
            "%s/%s [%s] context=%s",
            source_service,
            event_type,
            severity,
            context,
        )
        return False
    except RuntimeError as exc:
        # Loop teardown mid-emit ("Event loop is closed") looks like this.
        # Without the catch, the exception bubbles into run_coroutine_threadsafe
        # and the future never resolves, hanging the sync caller.
        logger.warning(
            "Health event asyncio_crash on emitter loop: "
            "%s/%s [%s] context=%s reason=%s",
            source_service,
            event_type,
            severity,
            context,
            exc,
        )
        return False


def _shutdown_emitter_loop() -> None:
    """Stop the emitter loop at interpreter exit."""
    global _emitter_stopped
    with _loop_lock:
        _emitter_stopped = True
        loop = _emitter_loop
    if loop is not None and not loop.is_closed():
        loop.call_soon_threadsafe(loop.stop)


atexit.register(_shutdown_emitter_loop)


def emit_health_event_sync(
    event_type: str,
    severity: str,
    source_service: str,
    context: dict[str, Any] | None = None,
) -> bool:
    """Sync-safe form of :func:`emit_health_event`. Never raises.

    The insert runs on the emitter's own long-lived loop and is awaited,
    so the row is committed before this function returns even when the
    caller sits inside a short-lived ``asyncio.run()`` — the case that
    previously dropped events (NFM-2241 C1).

    Prefer ``await emit_health_event(...)`` from ``async def`` code; this
    wrapper exists for genuinely synchronous call sites.

    Returns:
        ``True`` if the row was committed, ``False`` if the event was
        logged instead. Never raises, so callers may ignore the result.
    """
    event_type, severity, payload = _prepare(
        severity, source_service, event_type, context
    )

    loop = _get_emitter_loop()
    if loop is None:
        logger.warning(
            "Health event not persisted (emitter stopped, logging only): "
            "%s/%s [%s] context=%s",
            source_service,
            event_type,
            severity,
            payload,
        )
        return False

    try:
        future = asyncio.run_coroutine_threadsafe(
            _emit_on_emitter_loop(
                event_type=event_type,
                severity=severity,
                source_service=source_service,
                context=payload,
            ),
            loop,
        )
        return future.result(timeout=SYNC_EMIT_TIMEOUT_SECONDS)
    except Exception as exc:
        # Covers the wait timing out, the loop dying, and any insert error
        # that escaped ``emit_health_event``. The payload still reaches the
        # log, so an unpersisted event always leaves a trace.
        logger.warning(
            "Health event not confirmed (logging fallback): "
            "%s/%s [%s] context=%s reason=%s",
            source_service,
            event_type,
            severity,
            payload,
            exc,
        )
        return False


__all__ = [
    "EVENT_ASYNCIO_CRASH",
    "EVENT_CATEGORY_COERCION_FAIL",
    "EVENT_FALLBACK_TRIGGERED",
    "EVENT_GENERIC_SILENT_CATCH",
    "EVENT_VALIDATION_DROP",
    "SEVERITY_CRITICAL",
    "SEVERITY_ERROR",
    "SEVERITY_INFO",
    "SEVERITY_WARNING",
    "SYNC_EMIT_TIMEOUT_SECONDS",
    "VALID_EVENT_TYPES",
    "VALID_SEVERITIES",
    "build_context",
    "emit_health_event",
    "emit_health_event_sync",
]
