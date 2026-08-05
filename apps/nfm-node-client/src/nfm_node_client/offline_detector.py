"""Offline detector for hub reachability.

Provides async connectivity checking against the hub URL with
automatic state tracking (ONLINE / OFFLINE) and a callback
mechanism for state transitions. A background monitor loop can
be started to poll the hub at a configurable interval.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from enum import Enum

import httpx


_LOGGER = logging.getLogger("nfm_node_client.offline_detector")

_DEFAULT_CHECK_INTERVAL = 30.0
_DEFAULT_TIMEOUT = 5.0
_HEALTH_PATH = "/api/v1/health"


class ConnectionState(str, Enum):
    """Current hub connection state."""

    ONLINE = "online"
    OFFLINE = "offline"


class OfflineDetector:
    """Detect hub reachability and track online/offline state.

    Parameters
    ----------
    hub_url:
        Base URL of the hub to monitor.
    check_interval:
        Seconds between background connectivity checks (default 30s).
    timeout:
        HTTP timeout for each health check (default 5s).
    """

    def __init__(
        self,
        hub_url: str,
        *,
        check_interval: float = _DEFAULT_CHECK_INTERVAL,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        if not hub_url or not hub_url.strip():
            raise ValueError("hub_url is required")
        if check_interval <= 0:
            raise ValueError(f"check_interval must be > 0, got {check_interval}")
        if timeout <= 0:
            raise ValueError(f"timeout must be > 0, got {timeout}")

        self._hub_url = hub_url.rstrip("/")
        self._check_interval = float(check_interval)
        self._timeout = float(timeout)
        self._state = ConnectionState.ONLINE
        self._closed = False
        self._monitor_task: asyncio.Task[None] | None = None
        self._stop_event: asyncio.Event | None = None

        # Callable invoked on state transitions: (old_state, new_state)
        self.on_state_change: Callable[[ConnectionState, ConnectionState], None] = (
            lambda old, new: None
        )

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def hub_url(self) -> str:
        """Base URL of the hub being monitored."""
        return self._hub_url

    @property
    def state(self) -> ConnectionState:
        """Current connection state (ONLINE or OFFLINE)."""
        return self._state

    @property
    def is_online(self) -> bool:
        """Whether the hub is currently reachable."""
        return self._state == ConnectionState.ONLINE

    @property
    def is_offline(self) -> bool:
        """Whether the hub is currently unreachable."""
        return self._state == ConnectionState.OFFLINE

    # ------------------------------------------------------------------
    # Connectivity check
    # ------------------------------------------------------------------

    async def check_now(self) -> bool:
        """Perform an immediate connectivity check against the hub.

        Returns True if the hub is reachable (any non-5xx response),
        False if unreachable or server error. Updates ``state`` and
        fires ``on_state_change`` on transitions.
        """
        if self._closed:
            raise RuntimeError("OfflineDetector is closed")

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(f"{self._hub_url}{_HEALTH_PATH}")

            if response.status_code >= 500:
                reachable = False
            else:
                reachable = True

        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError):
            reachable = False

        new_state = ConnectionState.ONLINE if reachable else ConnectionState.OFFLINE
        if new_state != self._state:
            old_state = self._state
            self._state = new_state
            _LOGGER.info("connection state: %s -> %s", old_state.value, new_state.value)
            try:
                self.on_state_change(old_state, new_state)
            except Exception:  # noqa: BLE001 — callback errors must not propagate
                _LOGGER.warning("on_state_change callback raised", exc_info=True)

        return reachable

    # ------------------------------------------------------------------
    # Background monitor loop
    # ------------------------------------------------------------------

    async def start_monitor(self) -> None:
        """Start a background task that periodically checks hub reachability.

        Idempotent: calling this twice does not start a second loop.
        """
        if self._monitor_task is not None and not self._monitor_task.done():
            return
        self._stop_event = asyncio.Event()
        interval = self._check_interval
        stop_event = self._stop_event

        async def loop() -> None:
            assert stop_event is not None
            while not stop_event.is_set():
                try:
                    await self.check_now()
                except Exception:  # noqa: BLE001 — best-effort monitoring
                    _LOGGER.warning("monitor check failed", exc_info=True)
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=interval)
                except asyncio.TimeoutError:
                    continue

        self._monitor_task = asyncio.create_task(loop())

    async def stop_monitor(self) -> None:
        """Stop the background monitor loop. Safe to call when not running."""
        if self._stop_event is not None:
            self._stop_event.set()
        if self._monitor_task is not None:
            try:
                await asyncio.wait_for(self._monitor_task, timeout=5.0)
            except asyncio.TimeoutError:
                self._monitor_task.cancel()
            except Exception:  # noqa: BLE001
                pass
            self._monitor_task = None
        self._stop_event = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Mark the detector as closed. Idempotent."""
        if self._closed:
            return
        self._closed = True


__all__ = [
    "ConnectionState",
    "OfflineDetector",
]
