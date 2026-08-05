"""Tests for nfm_node_client.offline_detector — hub reachability detection."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest

from nfm_node_client.offline_detector import (
    ConnectionState,
    OfflineDetector,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

HUB_URL = "https://hub.example.test"


@pytest.fixture
def detector() -> OfflineDetector:
    """Return an OfflineDetector with default settings."""
    return OfflineDetector(hub_url=HUB_URL, check_interval=60.0)


# ---------------------------------------------------------------------------
# ConnectionState
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_connection_state_values() -> None:
    """ConnectionState enum has ONLINE and OFFLINE."""
    assert ConnectionState.ONLINE.value == "online"
    assert ConnectionState.OFFLINE.value == "offline"


# ---------------------------------------------------------------------------
# OfflineDetector — initialisation
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_detector_defaults_online(detector: OfflineDetector) -> None:
    """Detector starts in ONLINE state by default."""
    assert detector.state == ConnectionState.ONLINE


@pytest.mark.unit
def test_detector_hub_url(detector: OfflineDetector) -> None:
    """Detector exposes the configured hub_url."""
    assert detector.hub_url == HUB_URL


# ---------------------------------------------------------------------------
# OfflineDetector — is_online / is_offline
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_is_online_true_by_default(detector: OfflineDetector) -> None:
    """is_online returns True when state is ONLINE."""
    assert detector.is_online is True


@pytest.mark.unit
def test_is_offline_false_by_default(detector: OfflineDetector) -> None:
    """is_offline returns False when state is ONLINE."""
    assert detector.is_offline is False


# ---------------------------------------------------------------------------
# OfflineDetector — check_now (sync)
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_check_now_online_when_reachable(
    detector: OfflineDetector, monkeypatch: pytest.MonkeyPatch
) -> None:
    """check_now returns True when hub responds with 200."""
    mock_get = AsyncMock(return_value=httpx.Response(200))
    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)
    result = await detector.check_now()
    assert result is True
    assert detector.state == ConnectionState.ONLINE


@pytest.mark.unit
async def test_check_now_offline_on_connection_error(
    detector: OfflineDetector, monkeypatch: pytest.MonkeyPatch
) -> None:
    """check_now returns False when hub is unreachable."""
    mock_get = AsyncMock(side_effect=httpx.ConnectError("connection refused"))
    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)
    result = await detector.check_now()
    assert result is False
    assert detector.state == ConnectionState.OFFLINE


@pytest.mark.unit
async def test_check_now_offline_on_timeout(
    detector: OfflineDetector, monkeypatch: pytest.MonkeyPatch
) -> None:
    """check_now returns False on timeout."""
    mock_get = AsyncMock(side_effect=httpx.TimeoutException("timed out"))
    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)
    result = await detector.check_now()
    assert result is False
    assert detector.state == ConnectionState.OFFLINE


@pytest.mark.unit
async def test_check_now_offline_on_5xx(
    detector: OfflineDetector, monkeypatch: pytest.MonkeyPatch
) -> None:
    """check_now returns False when hub returns 5xx."""
    mock_get = AsyncMock(return_value=httpx.Response(503))
    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)
    result = await detector.check_now()
    assert result is False
    assert detector.state == ConnectionState.OFFLINE


@pytest.mark.unit
async def test_check_now_online_on_4xx(
    detector: OfflineDetector, monkeypatch: pytest.MonkeyPatch
) -> None:
    """check_now returns True on 4xx (hub is reachable, just auth issue)."""
    mock_get = AsyncMock(return_value=httpx.Response(401))
    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)
    result = await detector.check_now()
    assert result is True
    assert detector.state == ConnectionState.ONLINE


# ---------------------------------------------------------------------------
# OfflineDetector — state change callbacks
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_state_change_callback_on_offline(
    detector: OfflineDetector, monkeypatch: pytest.MonkeyPatch
) -> None:
    """on_state_change callback fires when going offline."""
    changes: list[tuple[ConnectionState, ConnectionState]] = []
    detector.on_state_change = lambda old, new: changes.append((old, new))

    mock_get = AsyncMock(side_effect=httpx.ConnectError("refused"))
    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)
    await detector.check_now()

    assert len(changes) == 1
    assert changes[0] == (ConnectionState.ONLINE, ConnectionState.OFFLINE)


@pytest.mark.unit
async def test_state_change_callback_on_reconnect(
    detector: OfflineDetector, monkeypatch: pytest.MonkeyPatch
) -> None:
    """on_state_change callback fires when coming back online."""
    changes: list[tuple[ConnectionState, ConnectionState]] = []
    detector.on_state_change = lambda old, new: changes.append((old, new))

    # First go offline
    mock_fail = AsyncMock(side_effect=httpx.ConnectError("refused"))
    monkeypatch.setattr(httpx.AsyncClient, "get", mock_fail)
    await detector.check_now()
    assert len(changes) == 1

    # Then come back online
    mock_ok = AsyncMock(return_value=httpx.Response(200))
    monkeypatch.setattr(httpx.AsyncClient, "get", mock_ok)
    await detector.check_now()
    assert len(changes) == 2
    assert changes[1] == (ConnectionState.OFFLINE, ConnectionState.ONLINE)


@pytest.mark.unit
async def test_no_callback_when_state_unchanged(
    detector: OfflineDetector, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No callback when state doesn't change."""
    callback_calls: list[None] = []
    detector.on_state_change = lambda old, new: callback_calls.append(None)

    mock_ok = AsyncMock(return_value=httpx.Response(200))
    monkeypatch.setattr(httpx.AsyncClient, "get", mock_ok)
    await detector.check_now()
    await detector.check_now()  # same state, no callback
    assert len(callback_calls) == 0


@pytest.mark.unit
async def test_state_change_callback_default_is_noop(
    detector: OfflineDetector, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Default on_state_change is a no-op (doesn't raise)."""
    mock_fail = AsyncMock(side_effect=httpx.ConnectError("refused"))
    monkeypatch.setattr(httpx.AsyncClient, "get", mock_fail)
    await detector.check_now()  # should not raise
    assert detector.state == ConnectionState.OFFLINE


# ---------------------------------------------------------------------------
# OfflineDetector — start / stop monitoring loop
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_start_monitor_creates_task(detector: OfflineDetector) -> None:
    """start_monitor creates a background task."""
    await detector.start_monitor()
    assert detector._monitor_task is not None
    assert not detector._monitor_task.done()
    await detector.stop_monitor()


@pytest.mark.unit
async def test_start_monitor_idempotent(detector: OfflineDetector) -> None:
    """Starting monitor twice doesn't create two tasks."""
    await detector.start_monitor()
    task1 = detector._monitor_task
    await detector.start_monitor()
    assert detector._monitor_task is task1
    await detector.stop_monitor()


@pytest.mark.unit
async def test_stop_monitor_is_idempotent(detector: OfflineDetector) -> None:
    """Stopping monitor when not running is a no-op."""
    await detector.stop_monitor()  # no error


@pytest.mark.unit
async def test_stop_monitor_cancels_task(
    detector: OfflineDetector, monkeypatch: pytest.MonkeyPatch
) -> None:
    """stop_monitor cancels the background task."""
    mock_get = AsyncMock(return_value=httpx.Response(200))
    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)
    await detector.start_monitor()
    await detector.stop_monitor()
    assert detector._monitor_task is None


@pytest.mark.unit
async def test_close_after_stop_monitor(detector: OfflineDetector) -> None:
    """close() after stop_monitor cleans up all state."""
    await detector.start_monitor()
    await detector.stop_monitor()
    detector.close()
    assert detector._monitor_task is None
    assert detector._closed is True


# ---------------------------------------------------------------------------
# OfflineDetector — close / closed
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_close_is_idempotent(detector: OfflineDetector) -> None:
    """close() is safe to call multiple times."""
    detector.close()
    detector.close()


@pytest.mark.unit
async def test_check_after_close_raises(detector: OfflineDetector) -> None:
    """check_now raises RuntimeError after close."""
    detector.close()
    with pytest.raises(RuntimeError, match="closed"):
        await detector.check_now()
