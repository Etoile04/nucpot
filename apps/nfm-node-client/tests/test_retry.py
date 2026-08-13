"""Tests for nfm_node_client.retry — exponential backoff."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
import pytest

from nfm_node_client.exceptions import (
    HeartbeatError,
    RetriesExhaustedError,
    UploadError,
)
from nfm_node_client.retry import (
    RetryPolicy,
    compute_backoff_delay,
    retry_async,
)


# ---------------------------------------------------------------------------
# compute_backoff_delay
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("attempt", "base", "maximum", "expected"),
    [
        # attempt=0 → base * 2^0 = base
        (0, 0.5, 30.0, 0.5),
        (1, 0.5, 30.0, 1.0),
        (2, 0.5, 30.0, 2.0),
        (3, 0.5, 30.0, 4.0),
        # attempt=8 → 0.5 * 256 = 128 → capped at 30
        (8, 0.5, 30.0, 30.0),
        (10, 0.5, 30.0, 30.0),
    ],
)
def test_compute_backoff_delay_caps_at_maximum(
    attempt: int, base: float, maximum: float, expected: float
) -> None:
    """Exponential growth from base, capped at maximum."""
    assert compute_backoff_delay(attempt, base=base, maximum=maximum) == expected


@pytest.mark.unit
def test_compute_backoff_delay_returns_float() -> None:
    """Backoff is always a float, even when attempt values are small ints."""
    delay = compute_backoff_delay(0, base=0.1, maximum=10.0)
    assert isinstance(delay, float)
    assert delay == pytest.approx(0.1)


# ---------------------------------------------------------------------------
# RetryPolicy
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_retry_policy_defaults() -> None:
    """RetryPolicy defaults match the spec: 3 retries, base=0.5, max=30."""
    policy = RetryPolicy()
    assert policy.max_retries == 3
    assert policy.backoff_base == 0.5
    assert policy.backoff_max == 30.0


@pytest.mark.unit
def test_retry_policy_custom_values() -> None:
    """RetryPolicy accepts custom overrides."""
    policy = RetryPolicy(max_retries=5, backoff_base=1.0, backoff_max=60.0)
    assert policy.max_retries == 5
    assert policy.backoff_base == 1.0
    assert policy.backoff_max == 60.0


@pytest.mark.unit
def test_retry_policy_rejects_negative_max_retries() -> None:
    """RetryPolicy raises ValueError on negative max_retries."""
    with pytest.raises(ValueError, match="max_retries"):
        RetryPolicy(max_retries=-1)


@pytest.mark.unit
def test_retry_policy_rejects_zero_backoff_base() -> None:
    """RetryPolicy rejects zero or negative backoff base."""
    with pytest.raises(ValueError, match="backoff_base"):
        RetryPolicy(backoff_base=0.0)
    with pytest.raises(ValueError, match="backoff_base"):
        RetryPolicy(backoff_base=-0.1)


# ---------------------------------------------------------------------------
# retry_async
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_retry_async_succeeds_first_try() -> None:
    """Function succeeds on first call — no retries."""
    calls = 0

    async def op() -> str:
        nonlocal calls
        calls += 1
        return "ok"

    result = await retry_async(op, RetryPolicy(max_retries=3))
    assert result == "ok"
    assert calls == 1


@pytest.mark.unit
async def test_retry_async_eventually_succeeds() -> None:
    """Function fails twice, then succeeds on 3rd attempt."""
    calls = 0

    async def op() -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise httpx.ConnectError("transient")
        return "ok"

    policy = RetryPolicy(max_retries=3, backoff_base=0.01, backoff_max=0.05)
    result = await retry_async(op, policy)
    assert result == "ok"
    assert calls == 3


@pytest.mark.unit
async def test_retry_async_raises_retries_exhausted() -> None:
    """All calls fail → re-raises the underlying exception (cause = RetriesExhaustedError)."""
    calls = 0

    async def op() -> str:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError(f"attempt {calls}")

    policy = RetryPolicy(max_retries=2, backoff_base=0.01, backoff_max=0.05)
    with pytest.raises(httpx.ConnectError) as exc_info:
        await retry_async(op, policy)
    assert calls == 3  # 1 initial + 2 retries
    # RetriesExhaustedError is preserved as the chained cause.
    assert isinstance(exc_info.value.__cause__, RetriesExhaustedError)
    assert exc_info.value.__cause__.attempts == 3
    assert exc_info.value.__cause__.last_exception is exc_info.value


@pytest.mark.unit
async def test_retry_async_does_not_retry_non_transient() -> None:
    """Non-retryable errors propagate immediately without retry."""
    calls = 0

    async def op() -> str:
        nonlocal calls
        calls += 1
        raise ValueError("fatal")

    policy = RetryPolicy(max_retries=5, backoff_base=0.01, backoff_max=0.05)
    with pytest.raises(ValueError, match="fatal"):
        await retry_async(op, policy)
    assert calls == 1


@pytest.mark.unit
async def test_retry_async_custom_retryable() -> None:
    """Custom retryable predicate is respected."""
    calls = 0

    async def op() -> str:
        nonlocal calls
        calls += 1
        raise UploadError("server-side 5xx", status_code=503)

    policy = RetryPolicy(max_retries=3, backoff_base=0.01, backoff_max=0.05)
    retryable = lambda exc: isinstance(exc, UploadError)
    with pytest.raises(UploadError) as exc_info:
        await retry_async(op, policy, retryable=retryable)
    assert calls == 4
    assert isinstance(exc_info.value.__cause__, RetriesExhaustedError)
    assert exc_info.value.__cause__.attempts == 4


@pytest.mark.unit
async def test_retry_async_returns_value_on_success_after_5xx() -> None:
    """First call 5xx, second call success — returns the success."""
    calls = 0

    async def op() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise HeartbeatError("hub down", status_code=503)
        return "ack"

    policy = RetryPolicy(max_retries=3, backoff_base=0.01, backoff_max=0.05)
    result = await retry_async(op, policy)
    assert result == "ack"
    assert calls == 2


@pytest.mark.unit
async def test_retry_async_sleeps_between_attempts() -> None:
    """Sleep delay matches the exponential backoff schedule."""
    calls = 0
    start = time.monotonic()

    async def op() -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise httpx.ConnectError("transient")
        return "ok"

    policy = RetryPolicy(max_retries=3, backoff_base=0.05, backoff_max=1.0)
    await retry_async(op, policy, sleep=asyncio.sleep)
    elapsed = time.monotonic() - start
    # Two sleeps: 0.05 + 0.10 = 0.15s minimum
    assert elapsed >= 0.14


@pytest.mark.unit
async def test_retry_async_injectable_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sleep function is injectable for testing."""
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    async def op() -> None:
        raise httpx.ConnectError("fail")

    policy = RetryPolicy(max_retries=2, backoff_base=0.5, backoff_max=10.0)
    with pytest.raises(httpx.ConnectError):
        await retry_async(op, policy, sleep=fake_sleep)
    # Two retries → two sleeps
    assert sleeps == [pytest.approx(0.5), pytest.approx(1.0)]
