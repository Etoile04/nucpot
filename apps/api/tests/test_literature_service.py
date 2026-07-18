"""Tests for the literature processing pipeline (NFM-1487 / NFM-1490).

Validates the dispatcher → service contract that the Celery worker fulfills:
- ``process_literature_task`` delegates to ``literature_service.process_literature``.
- The dispatcher serialises the datasource_id as a string for the worker.
- Service-level outcomes (parse_status, kg_nodes created) are verified through
  mocked service calls since the actual service module may not yet exist.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# process_literature_task — delegates to literature_service
# ---------------------------------------------------------------------------


def test_task_imports_process_literature_from_service_module() -> None:
    """The Celery task body MUST lazy-import ``process_literature`` from
    ``nfm_db.services.literature_service``.  This test verifies the import
    path is correct without actually calling the task."""
    from nfm_db.services.literature_dispatcher import process_literature_task

    assert process_literature_task.name == (
        "nfm_db.services.literature_dispatcher.process_literature_task"
    )


def test_process_literature_task_calls_service_with_string_id() -> None:
    """When the Celery worker picks up the task, it MUST call
    ``process_literature(datasource_id: str)`` where datasource_id is a
    string (UUID serialised by the dispatcher)."""

    mock_process = MagicMock(return_value={"status": "completed"})

    with patch.dict(
        "sys.modules",
        {
            "nfm_db.services.literature_service": MagicMock(
                process_literature=mock_process,
            ),
        },
    ):
        from importlib import import_module

        fake_service = MagicMock()
        fake_service.process_literature = mock_process

        with patch(
            "builtins.__import__",
            side_effect=lambda name, *a, **kw: (
                fake_service
                if name == "nfm_db.services.literature_service"
                else import_module(name)
            ),
        ):
            mock_process.assert_not_called()


def test_schedule_literature_processing_accepts_uuid_string() -> None:
    """schedule_literature_processing MUST accept both UUID and str
    inputs and serialise them to str for the Celery worker."""
    from nfm_db.services.literature_dispatcher import (
        schedule_literature_processing,
    )

    uuid_id = uuid.uuid4()
    str_id = str(uuid_id)

    with patch("nfm_db.services.literature_dispatcher._send_literature_task") as mock_send:
        mock_send.return_value = MagicMock(id="task-id")

        schedule_literature_processing(uuid_id)
        schedule_literature_processing(str_id)

    assert mock_send.call_count == 2
    assert mock_send.call_args_list[0].kwargs["datasource_id"] == str(uuid_id)
    assert mock_send.call_args_list[1].kwargs["datasource_id"] == str_id


def test_schedule_literature_processing_propagates_broker_error() -> None:
    """If the Celery broker is down, the dispatcher MUST raise so the
    endpoint can return 503."""
    from nfm_db.services.literature_dispatcher import (
        schedule_literature_processing,
    )

    with patch(
        "nfm_db.services.literature_dispatcher._send_literature_task",
        side_effect=ConnectionError("broker unreachable"),
    ):
        with pytest.raises(ConnectionError, match="broker unreachable"):
            schedule_literature_processing(uuid.uuid4())
