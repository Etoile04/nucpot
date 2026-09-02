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
    prewarm_ollama_model,
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
    assert LITERATURE_TASK_NAME == ("nfm_db.services.literature_dispatcher.process_literature_task")


# ---------------------------------------------------------------------------
# schedule_literature_processing — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_schedule_dispatches_to_literature_processing_queue() -> None:
    """schedule_literature_processing MUST route to the literature_processing queue."""
    datasource_id = uuid.uuid4()

    with patch("nfm_db.services.literature_dispatcher._send_literature_task") as mock_send:
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

    with patch("nfm_db.services.literature_dispatcher._send_literature_task") as mock_send:
        mock_send.return_value = MagicMock(id="celery-task-id-abc")
        result = schedule_literature_processing(datasource_id)

    assert result == "celery-task-id-abc"


@pytest.mark.asyncio
async def test_schedule_accepts_uuid_or_string() -> None:
    """Both UUID and str inputs are accepted; the worker always gets a string."""
    uuid_id = uuid.uuid4()
    str_id = str(uuid_id)

    with patch("nfm_db.services.literature_dispatcher._send_literature_task") as mock_send:
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


# ---------------------------------------------------------------------------
# prewarm_ollama_model — NFM-3902 cold-load guard
# ---------------------------------------------------------------------------


def test_prewarm_skipped_when_provider_not_ollama() -> None:
    """Non-Ollama providers are already warm; pre-warm must short-circuit."""
    with patch.dict(
        "os.environ",
        {
            "LLM_PROVIDER": "openai",
            "LLM_BASE_URL": "https://api.example.com/v1",
            "LLM_MODEL": "gpt-4o-mini",
        },
        clear=False,
    ):
        result = prewarm_ollama_model()
    assert result == {"status": "skipped", "reason": "non-ollama-provider"}


def test_prewarm_skipped_when_ollama_env_missing() -> None:
    """If LLM_BASE_URL or LLM_MODEL is empty, pre-warm must skip cleanly."""
    with patch.dict(
        "os.environ",
        {"LLM_PROVIDER": "ollama", "LLM_BASE_URL": "", "LLM_MODEL": ""},
        clear=False,
    ):
        result = prewarm_ollama_model()
    assert result == {"status": "skipped", "reason": "non-ollama-provider"}


def test_prewarm_returns_ok_on_2xx() -> None:
    """A successful 200 response is recorded as ``status=ok`` with elapsed."""
    fake_response = MagicMock(status_code=200, text="")
    with patch.dict(
        "os.environ",
        {
            "LLM_PROVIDER": "ollama",
            "LLM_BASE_URL": "http://localhost:11434/v1",
            "LLM_MODEL": "qwen3.8:27b-mlx",
            "LLM_API_KEY": "ollama",
        },
        clear=False,
    ):
        with patch(
            "nfm_db.services.literature_dispatcher.httpx.Client"
        ) as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__.return_value.post.return_value = fake_response
            mock_client_cls.return_value = mock_client
            result = prewarm_ollama_model()

    assert result["status"] == "ok"
    assert result["model"] == "qwen3.8:27b-mlx"
    assert result["status_code"] == 200
    assert isinstance(result["elapsed_s"], float)
    # Payload pins model + max_tokens=1 — that's what actually triggers the
    # load without spending meaningful tokens.
    sent = mock_client.__enter__.return_value.post.call_args
    assert sent.args[0].endswith("/chat/completions")
    assert sent.kwargs["json"]["model"] == "qwen3.8:27b-mlx"
    assert sent.kwargs["json"]["max_tokens"] == 1


def test_prewarm_returns_http_error_on_5xx() -> None:
    """A 503 from Ollama must NOT raise — extraction proceeds without pre-warm."""
    fake_response = MagicMock(status_code=503, text="model not loaded")
    with patch.dict(
        "os.environ",
        {
            "LLM_PROVIDER": "ollama",
            "LLM_BASE_URL": "http://localhost:11434/v1",
            "LLM_MODEL": "qwen3.8:27b-mlx",
            "LLM_API_KEY": "ollama",
        },
        clear=False,
    ):
        with patch(
            "nfm_db.services.literature_dispatcher.httpx.Client"
        ) as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__.return_value.post.return_value = fake_response
            mock_client_cls.return_value = mock_client
            result = prewarm_ollama_model()

    assert result["status"] == "http_error"
    assert result["status_code"] == 503


def test_prewarm_returns_transport_error_on_connection_failure() -> None:
    """A network failure (Ollama down) must NOT raise — extraction proceeds."""
    import httpx

    with patch.dict(
        "os.environ",
        {
            "LLM_PROVIDER": "ollama",
            "LLM_BASE_URL": "http://localhost:11434/v1",
            "LLM_MODEL": "qwen3.8:27b-mlx",
            "LLM_API_KEY": "ollama",
        },
        clear=False,
    ):
        with patch(
            "nfm_db.services.literature_dispatcher.httpx.Client"
        ) as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__.return_value.post.side_effect = httpx.ConnectError(
                "connection refused"
            )
            mock_client_cls.return_value = mock_client
            result = prewarm_ollama_model()

    assert result["status"] == "transport_error"
    assert "connection refused" in result["error"]
