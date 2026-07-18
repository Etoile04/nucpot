"""Unit tests for the literature dispatcher (NFM-1489).

The dispatcher wires ``POST /literature/upload`` (and the future
``POST /literature/from-doi``) into the production Celery worker so the
HTTP request returns quickly while PDF/DOI parsing continues in the
background.

Contract:
    ``schedule_literature_processing(datasource_id)`` is fire-and-forget
    from the caller's perspective.  It MUST dispatch a Celery task to the
    ``literature_processing`` queue and return its task id so callers can
    surface "parse started" feedback.

The Celery task name and queue are wired into ``celery_app`` via
``task_routes`` so a worker started with ``--queues=literature_processing``
picks it up.  The dispatcher's role is to be a thin, idempotent entry point
that endpoints call.

These tests deliberately mock ``celery_app.send_task`` — we do not boot
RabbitMQ in unit tests, we only verify the dispatch contract.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest

from nfm_db.services.literature_dispatcher import (
    LITERATURE_TASK_NAME,
    schedule_literature_processing,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


EXPECTED_QUEUE = "literature_processing"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_mock_send_task():
    """Reset the patched ``send_task`` between tests."""
    yield


# ---------------------------------------------------------------------------
# Constants exposed by the dispatcher
# ---------------------------------------------------------------------------


def test_task_name_is_stable_for_endpoint_contract() -> None:
    """The task name MUST be stable — endpoints and tests rely on it."""
    assert LITERATURE_TASK_NAME == (
        "nfm_db.services.literature_dispatcher.process_literature_task"
    )


# ---------------------------------------------------------------------------
# schedule_literature_processing — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_schedule_dispatches_to_literature_processing_queue() -> None:
    """schedule_literature_processing MUST route to the literature_processing queue."""
    datasource_id = uuid.uuid4()

    with patch(
        "nfm_db.services.literature_dispatcher._send_literature_task"
    ) as mock_send:
        mock_send.return_value = MagicMock(id="celery-task-id-123")
        schedule_literature_processing(datasource_id)

    mock_send.assert_called_once()
    kwargs = mock_send.call_args.kwargs
    assert kwargs["queue"] == EXPECTED_QUEUE
    assert kwargs["task_name"] == LITERATURE_TASK_NAME
    # Serialized datasource id (str) is what the worker expects.
    assert kwargs["datasource_id"] == str(datasource_id)


@pytest.mark.asyncio
async def test_schedule_returns_task_id_for_caller() -> None:
    """Callers need a task id to put in the response body or logs."""
    datasource_id = uuid.uuid4()

    with patch(
        "nfm_db.services.literature_dispatcher._send_literature_task"
    ) as mock_send:
        mock_send.return_value = MagicMock(id="celery-task-id-abc")
        result = schedule_literature_processing(datasource_id)

    assert result == "celery-task-id-abc"


@pytest.mark.asyncio
async def test_schedule_accepts_uuid_or_string() -> None:
    """Both UUID and str inputs are accepted; the worker always gets a string."""
    uuid_id = uuid.uuid4()
    str_id = str(uuid_id)

    with patch(
        "nfm_db.services.literature_dispatcher._send_literature_task"
    ) as mock_send:
        mock_send.return_value = MagicMock(id="x")
        schedule_literature_processing(uuid_id)
        schedule_literature_processing(str_id)

    assert mock_send.call_count == 2
    assert mock_send.call_args_list[0].kwargs["datasource_id"] == str(uuid_id)
    assert mock_send.call_args_list[1].kwargs["datasource_id"] == str_id


@pytest.mark.asyncio
async def test_schedule_does_not_silently_swallow_broker_errors() -> None:
    """If Celery is down, the dispatcher MUST raise — the endpoint can then
    surface a 503 to the user.  It MUST NOT silently swallow the error."""
    datasource_id = uuid.uuid4()

    with patch(
        "nfm_db.services.literature_dispatcher._send_literature_task",
        side_effect=RuntimeError("redis broker down"),
    ):
        with pytest.raises(RuntimeError, match="redis broker down"):
            schedule_literature_processing(datasource_id)
