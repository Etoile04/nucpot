"""ADR-009 §4.3-b Paperclip wake service (NFM-3688).

Consumes :class:`WakeIntent` rows emitted by
:mod:`nfm_db.services.adr009_reconcile_routine` and posts a wake
notification to the Paperclip ``POST /api/agents/{id}/wakeup``
endpoint so the dependent's assignee receives a fresh heartbeat.

Operational contract:

* **Post-commit, best-effort.** The wake HTTP call is wrapped in a
  try/except that catches every exception (network errors, timeouts,
  non-2xx responses) and converts it to a logged warning. A wake
  failure MUST NOT roll back the audit row or the auto-transition;
  the §4.3-c audit log is the durable record.
* **Idempotent.** Each :class:`WakeIntent` carries a deterministic
  ``idempotency_key`` derived from the
  ``(dependent_id, sorted_cleared_blocker_ids)`` tuple, so a partial-
  failure retry of the daily cron driver never double-wakes the
  assignee.
* **No-assignee edge.** The routine filters dependents without an
  ``assignee_agent_id`` before emitting a :class:`WakeIntent`, so the
  wake service can rely on ``intent.assignee_agent_id`` being set.
* **Multi-tenant safe.** The wake target is the dependent's agent
  UUID; the routine never cross-wakes between companies because
  :class:`WakeIntent` is scoped to a single dependent row.

Why a dedicated module instead of inlining the HTTP call in the
reconcile routine? Keeping the wake service separate lets the
routine stay pure (no I/O, deterministic, easy to dry-run) and lets
the cron driver wrap wake emissions in transaction boundaries it
controls (``session.commit()`` → fire intents → log failures).

References:

* NFM-3519 §4.3 — source spec.
* NFM-3571 §4.1-c — analogous reference implementation pattern
  (audit writer + flag, no scanner).
* NFM-3586 §4.3-c — audit writer (this service's downstream sibling).
* NFM-3688 — §4.3-b auto-transition + wake (this file's parent
  scope).
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nfm_db.services.adr009_reconcile_routine import WakeIntent

logger = logging.getLogger(__name__)


#: HTTP status codes that count as a successful wake. Anything outside
#: this set is logged and treated as a best-effort failure so the
#: audit row stays durable even when Paperclip is degraded.
_SUCCESS_STATUSES: frozenset[int] = frozenset({200, 201, 202, 204})


def _build_wakeup_payload(intent: WakeIntent) -> dict[str, object]:
    """Shape the POST body for ``/api/agents/{id}/wakeup``.

    Mirrors the OpenAPI schema:

    * ``source`` — ``"automation"`` because §4.3-b is the cron driver
      firing the wake, not a board user.
    * ``triggerDetail`` — ``"system"`` because the trigger is the
      daily reconcile routine, not a manual callback.
    * ``reason`` — human-readable string the agent sees in its
      heartbeat payload.
    * ``payload`` — structured data the agent can use to route the
      wake (dependent identifier, transition, cleared blockers).
    * ``idempotencyKey`` — deterministic, sourced from the intent.
    """
    return {
        "source": "automation",
        "triggerDetail": "system",
        "reason": (
            f"ADR-009 §4.3-b auto-transition: dependent "
            f"{intent.dependent_identifier} cleared all blockers; "
            f"transitioning {intent.status_transition['from']} -> "
            f"{intent.status_transition['to']}"
        ),
        "payload": {
            "routine": "adr009-daily-reconcile",
            "section": "4.3-b",
            "dependent_id": str(intent.dependent_id),
            "dependent_identifier": intent.dependent_identifier,
            "cleared_blocker_ids": [str(b) for b in intent.cleared_blocker_ids],
            "status_transition": intent.status_transition,
        },
        "idempotencyKey": intent.idempotency_key,
    }


def _resolve_paperclip_base_url() -> str:
    """Return the Paperclip API base URL.

    Defaults to ``http://paperclip-api:3101`` (the docker-compose
    service hostname used by the nucpot production stack). Tests and
    local development override via ``PAPERCLIP_API_URL``.
    """
    return (
        os.environ.get("PAPERCLIP_API_URL")
        or os.environ.get("PAPERCLIP_BASE_URL")
        or "http://paperclip-api:3101"
    ).rstrip("/")


def _resolve_paperclip_api_key() -> str | None:
    """Return the Paperclip API key for outbound calls.

    The Paperclip runtime injects ``PAPERCLIP_API_KEY`` automatically;
    we surface it via ``os.environ`` so nucpot workers see the same
    env var the agent runtime provides.
    """
    raw = os.environ.get("PAPERCLIP_API_KEY")
    if not raw:
        return None
    return raw.strip() or None


def fire_wake_intent(intent: WakeIntent, *, timeout: float = 5.0) -> bool:
    """Fire one §4.3-b wake via Paperclip's wakeup endpoint.

    Best-effort: any failure (missing config, network error, non-2xx
    response) is logged at WARNING level and the function returns
    ``False``. The audit row + auto-transition remain committed; the
    daily cron driver can retry the wake on the next run via the
    deterministic ``idempotencyKey``.

    Returns
    -------
    bool
        ``True`` iff the Paperclip API acknowledged the wake with a
        2xx response. ``False`` for any failure mode (including
        missing config) so callers can record the outcome without
        distinguishing error shapes.
    """
    api_key = _resolve_paperclip_api_key()
    if api_key is None:
        logger.warning(
            "adr009 wake skipped: PAPERCLIP_API_KEY not set "
            "(dependent=%s, idempotencyKey=%s)",
            intent.dependent_identifier,
            intent.idempotency_key,
        )
        return False

    base_url = _resolve_paperclip_base_url()
    url = f"{base_url}/api/agents/{intent.assignee_agent_id}/wakeup"

    # Import httpx lazily so module import does not require httpx to
    # be installed in environments that don't run the cron driver
    # (e.g. the test fixture under sqlite-only harnesses).
    try:
        import httpx
    except ImportError:
        logger.warning(
            "adr009 wake skipped: httpx is not installed "
            "(dependent=%s, idempotencyKey=%s)",
            intent.dependent_identifier,
            intent.idempotency_key,
        )
        return False

    body = _build_wakeup_payload(intent)

    try:
        response = httpx.post(
            url,
            json=body,
            headers={
                "Authorization": f"Bearer {api_key}",
                "X-Paperclip-Origin": "adr009-daily-reconcile",
            },
            timeout=timeout,
        )
    except httpx.HTTPError as exc:
        logger.warning(
            "adr009 wake failed (network error): dependent=%s "
            "idempotencyKey=%s error=%s",
            intent.dependent_identifier,
            intent.idempotency_key,
            exc,
        )
        return False
    except Exception as exc:
        logger.warning(
            "adr009 wake failed (unexpected): dependent=%s "
            "idempotencyKey=%s error=%s",
            intent.dependent_identifier,
            intent.idempotency_key,
            exc,
        )
        return False

    if response.status_code not in _SUCCESS_STATUSES:
        logger.warning(
            "adr009 wake failed (HTTP %s): dependent=%s "
            "idempotencyKey=%s body=%s",
            response.status_code,
            intent.dependent_identifier,
            intent.idempotency_key,
            response.text[:512],
        )
        return False

    logger.info(
        "adr009 wake fired: dependent=%s transition=%s->%s "
        "idempotencyKey=%s",
        intent.dependent_identifier,
        intent.status_transition["from"],
        intent.status_transition["to"],
        intent.idempotency_key,
    )
    return True


def fire_wake_intents(
    intents: list[WakeIntent] | tuple[WakeIntent, ...],
    *,
    timeout: float = 5.0,
) -> tuple[int, int]:
    """Fire a batch of wake intents.

    Convenience wrapper for the cron driver: takes the
    :attr:`ReconcileResult.wake_intents` tuple, fires each one
    independently, and returns a ``(succeeded, failed)`` count so the
    caller can log a summary line.

    Failures are best-effort (each :func:`fire_wake_intent` swallows
    its own exceptions). The batch never raises.
    """
    succeeded = 0
    failed = 0
    for intent in intents:
        if fire_wake_intent(intent, timeout=timeout):
            succeeded += 1
        else:
            failed += 1
    if intents:
        logger.info(
            "adr009 wake batch: %d succeeded, %d failed (of %d)",
            succeeded,
            failed,
            len(intents),
        )
    return succeeded, failed


__all__ = [
    "fire_wake_intent",
    "fire_wake_intents",
]
