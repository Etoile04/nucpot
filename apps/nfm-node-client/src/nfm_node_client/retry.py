"""Exponential backoff retry policy and runner.

The default policy matches the W2 spec: 3 retries with exponential backoff
starting at 0.5s and capped at 30s. The runner is async and sleep is
injectable for fast tests.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, TypeVar

import httpx

from nfm_node_client.exceptions import (
    HeartbeatError,
    RegistrationError,
    RetriesExhaustedError,
    SyncStatusError,
    UploadError,
)


T = TypeVar("T")


@dataclass(frozen=True)
class RetryPolicy:
    """Exponential backoff retry policy.

    Delay for attempt *n* (0-indexed) is ``min(backoff_base * 2**n, backoff_max)``.
    ``max_retries`` is the number of *retries* after the initial attempt,
    so a policy with ``max_retries=3`` produces up to 4 total calls.
    """

    max_retries: int = 3
    backoff_base: float = 0.5
    backoff_max: float = 30.0

    def __post_init__(self) -> None:
        if self.max_retries < 0:
            raise ValueError(f"max_retries must be >= 0, got {self.max_retries}")
        if self.backoff_base <= 0:
            raise ValueError(f"backoff_base must be > 0, got {self.backoff_base}")
        if self.backoff_max < self.backoff_base:
            raise ValueError(
                f"backoff_max ({self.backoff_max}) must be >= backoff_base "
                f"({self.backoff_base})"
            )


def compute_backoff_delay(
    attempt: int, *, base: float = 0.5, maximum: float = 30.0
) -> float:
    """Return the backoff delay for retry attempt *n* (0-indexed).

    Delay is ``min(base * 2**n, maximum)``. Used by ``retry_async`` and
    exposed for callers that want to surface the next retry ETA.
    """
    if attempt < 0:
        raise ValueError(f"attempt must be >= 0, got {attempt}")
    return float(min(base * (2 ** attempt), maximum))


def _default_retryable(exc: BaseException) -> bool:
    """Default predicate: retry on transient network/HTTP errors.

    Retry on ``httpx`` transport errors (timeouts, connection resets)
    and 5xx HTTP responses encoded as NfmNodeClientError subclasses.
    """
    if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError)):
        return True
    if isinstance(
        exc,
        (RegistrationError, HeartbeatError, SyncStatusError, UploadError),
    ):
        status = getattr(exc, "status_code", None)
        if status is None or status >= 500:
            return True
    return False


async def retry_async(
    op: Callable[[], Awaitable[T]],
    policy: RetryPolicy,
    *,
    retryable: Callable[[BaseException], bool] = _default_retryable,
    sleep: Callable[[float], Any] = asyncio.sleep,
) -> T:
    """Invoke *op* with exponential backoff retries.

    On non-retryable exceptions the original exception propagates
    unchanged. If all attempts fail with retryable exceptions, a
    :class:`RetriesExhaustedError` is raised carrying the last exception.
    """
    last_exception: BaseException | None = None
    for attempt in range(policy.max_retries + 1):
        try:
            return await op()
        except BaseException as exc:  # noqa: BLE001 - we filter via retryable
            if not retryable(exc):
                raise
            last_exception = exc
            if attempt >= policy.max_retries:
                break
            await sleep(
                compute_backoff_delay(
                    attempt,
                    base=policy.backoff_base,
                    maximum=policy.backoff_max,
                )
            )

    # All attempts exhausted — re-raise the last underlying exception so
    # callers see the typed error (e.g. HeartbeatError, UploadError).
    # The RetriesExhaustedError diagnostic is preserved via __cause__.
    assert last_exception is not None  # for type-checkers
    attempts = policy.max_retries + 1
    diagnostic = RetriesExhaustedError(
        f"retries exhausted after {attempts} attempts",
        attempts=attempts,
        last_exception=last_exception,
    )
    raise last_exception from diagnostic


__all__ = [
    "RetryPolicy",
    "compute_backoff_delay",
    "retry_async",
]
