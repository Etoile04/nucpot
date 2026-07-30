"""Health event emitter for structured silent-failure tracking (NFM-2220).

Replaces bare ``except: pass`` blocks so that a swallowed failure always
leaves an observable trace.

Two entry points are provided:

``emit_health_event()``
    Async. Use from ``async def`` call sites.

``emit_health_event_sync()``
    Sync-safe wrapper for the silent-catch sites that live in
    synchronous code (SSH cleanup, SFTP helpers, Celery prefork bodies).

Both are **best effort and never raise**. A health event must never
become the reason a request fails — if the insert cannot be done the
event is written to the log instead, so the information is still
recoverable.

The emitter deliberately opens its **own** session via
``async_session_factory`` rather than reusing the caller's. Several call
sites emit precisely because the caller's session has just failed (e.g. a
rollback error), so reusing it would lose the event.

Usage::

    from nfm_db.services.health_event_emitter import (
        SEVERITY_WARNING,
        emit_health_event,
    )

    try:
        risky_operation()
    except Exception as exc:
        await emit_health_event(
            event_type="fallback_triggered",
            severity=SEVERITY_WARNING,
            source_service="mineru_extraction",
            context={"error": str(exc)},
        )
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from nfm_db.database import async_session_factory
from nfm_db.models.health_event import HealthEvent

logger = logging.getLogger(__name__)

# Severity values accepted by the ``health_events.severity`` column.
SEVERITY_INFO = "info"
SEVERITY_WARNING = "warning"
SEVERITY_ERROR = "error"
SEVERITY_CRITICAL = "critical"

VALID_SEVERITIES = frozenset(
    {SEVERITY_INFO, SEVERITY_WARNING, SEVERITY_ERROR, SEVERITY_CRITICAL}
)

# Tasks scheduled by ``emit_health_event_sync`` are kept here so the event
# loop cannot garbage-collect them mid-flight.
_BACKGROUND_TASKS: set[asyncio.Task[Any]] = set()


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
) -> tuple[str, dict[str, Any]]:
    """Copy the context, stamp it, and validate severity.

    The context is copied rather than mutated so the caller's dict does
    not gain a ``timestamp`` key as a side effect.
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

    return severity, payload


async def emit_health_event(
    event_type: str,
    severity: str,
    source_service: str,
    context: dict[str, Any] | None = None,
) -> bool:
    """Record a structured health event. Never raises.

    Args:
        event_type: Event category, e.g. ``"fallback_triggered"``.
        severity: One of :data:`VALID_SEVERITIES`.
        source_service: Service that caught the exception, e.g.
            ``"mineru_extraction"``.
        context: Structured metadata stored in the JSONB column.

    Returns:
        ``True`` if the row was committed, ``False`` if the emitter fell
        back to logging.
    """
    severity, payload = _prepare(severity, source_service, event_type, context)

    try:
        async with async_session_factory() as session:
            session.add(
                HealthEvent(
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


def emit_health_event_sync(
    event_type: str,
    severity: str,
    source_service: str,
    context: dict[str, Any] | None = None,
) -> None:
    """Sync-safe form of :func:`emit_health_event`. Never raises.

    When an event loop is running the insert is scheduled as a background
    task. When there is no loop — a plain synchronous worker — the event
    is logged rather than driven through a throwaway loop, because these
    call sites are cleanup/teardown paths that must not block on database
    I/O.
    """
    severity, payload = _prepare(severity, source_service, event_type, context)

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.warning(
            "Health event (no running loop, logging only): %s/%s [%s] context=%s",
            source_service,
            event_type,
            severity,
            payload,
        )
        return

    task = loop.create_task(
        emit_health_event(
            event_type=event_type,
            severity=severity,
            source_service=source_service,
            context=payload,
        )
    )
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
