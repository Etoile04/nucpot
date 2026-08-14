"""Burst/debounce behaviour for backup-refusal SRE alerts — NFM-3024-E AC4.

Why a constant of N = 2
-----------------------
The debounce policy under test (see
``nfm_db.monitoring.refusal_alert_debounce``) is hour-bucketed:

1. the *first* refusal in a wall-clock hour alerts immediately, so SRE hears
   about the condition within one heartbeat; and
2. everything else in that hour is suppressed and rolled into *one* summary
   emitted when the hour closes.

That is the smallest constant that still satisfies both halves of the
requirement — dropping to 1 would mean either no immediate alert (SRE finds
out an hour late) or no count of what was suppressed (SRE can't tell 2
refusals from 200). So ``N = MAX_ALERTS_PER_HOUR = 2``, independent of burst
size.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from nfm_db.monitoring.refusal_alert_debounce import (
    MAX_ALERTS_PER_HOUR,
    RefusalAlert,
    RefusalAlertDebouncer,
)

# Fixed synthetic clock origin — no coupling to production timing.
HOUR_T = datetime(2026, 8, 13, 9, 0, 0)
HOUR_T_PLUS_1 = HOUR_T + timedelta(hours=1)

BURST_SIZE = 100


def _collecting_debouncer() -> tuple[RefusalAlertDebouncer, list[RefusalAlert]]:
    alerts: list[RefusalAlert] = []
    return RefusalAlertDebouncer(emit=alerts.append), alerts


def _drive_burst(
    debouncer: RefusalAlertDebouncer, *, start: datetime, count: int
) -> None:
    """Feed ``count`` refusals spread across the hour beginning at ``start``."""
    for i in range(count):
        # Spread across the hour without ever crossing into the next one.
        offset = timedelta(seconds=(i * 3600) // count)
        debouncer.observe_refusal(reason="capacity guardrail", now=start + offset)


def test_single_refusal_emits_exactly_one_warning() -> None:
    debouncer, alerts = _collecting_debouncer()

    debouncer.observe_refusal(reason="capacity guardrail", now=HOUR_T)
    debouncer.flush()

    assert len(alerts) == 1
    assert alerts[0].kind == "first"
    assert alerts[0].message.startswith("[SRE-WARNING]")


def test_burst_of_100_refusals_in_one_hour_is_debounced() -> None:
    """AC4: 100 sequential refusals produce <= N alerts, not 100."""
    debouncer, alerts = _collecting_debouncer()

    _drive_burst(debouncer, start=HOUR_T, count=BURST_SIZE)
    debouncer.flush()

    assert len(alerts) <= MAX_ALERTS_PER_HOUR
    assert [a.kind for a in alerts] == ["first", "summary"]
    assert all(a.message.startswith("[SRE-WARNING]") for a in alerts)

    summary = alerts[-1]
    assert summary.refusal_count == BURST_SIZE
    assert str(BURST_SIZE) in summary.message


def test_burst_alert_count_does_not_grow_with_burst_size() -> None:
    """The bound is on the policy, not on this particular burst size."""
    counts = []
    for size in (2, 25, BURST_SIZE, 500):
        debouncer, alerts = _collecting_debouncer()
        _drive_burst(debouncer, start=HOUR_T, count=size)
        debouncer.flush()
        counts.append(len(alerts))

    assert counts == [MAX_ALERTS_PER_HOUR] * len(counts)


def test_refusals_in_adjacent_hours_each_get_their_own_first_alert() -> None:
    """A refusal in hour T must not swallow the one in hour T+1."""
    debouncer, alerts = _collecting_debouncer()

    debouncer.observe_refusal(reason="capacity guardrail", now=HOUR_T)
    debouncer.observe_refusal(reason="capacity guardrail", now=HOUR_T_PLUS_1)
    debouncer.flush()

    # One lone refusal per hour: no suppression, so no summaries at all.
    assert [a.kind for a in alerts] == ["first", "first"]
    assert [a.hour for a in alerts] == [HOUR_T, HOUR_T_PLUS_1]


def test_bursts_in_adjacent_hours_are_debounced_independently() -> None:
    debouncer, alerts = _collecting_debouncer()

    _drive_burst(debouncer, start=HOUR_T, count=BURST_SIZE)
    _drive_burst(debouncer, start=HOUR_T_PLUS_1, count=BURST_SIZE)
    debouncer.flush()

    assert len(alerts) <= 2 * MAX_ALERTS_PER_HOUR
    assert [a.kind for a in alerts] == ["first", "summary", "first", "summary"]
    assert [a.hour for a in alerts] == [
        HOUR_T,
        HOUR_T,
        HOUR_T_PLUS_1,
        HOUR_T_PLUS_1,
    ]


def test_hour_rollover_flushes_summary_without_explicit_flush() -> None:
    """The summary for hour T lands as soon as hour T+1 opens."""
    debouncer, alerts = _collecting_debouncer()

    _drive_burst(debouncer, start=HOUR_T, count=BURST_SIZE)
    assert [a.kind for a in alerts] == ["first"]  # still open, nothing summarised

    debouncer.observe_refusal(reason="capacity guardrail", now=HOUR_T_PLUS_1)

    assert [a.kind for a in alerts] == ["first", "summary", "first"]
    assert alerts[1].hour == HOUR_T
    assert alerts[1].refusal_count == BURST_SIZE


def test_flush_is_idempotent() -> None:
    debouncer, alerts = _collecting_debouncer()

    _drive_burst(debouncer, start=HOUR_T, count=BURST_SIZE)
    debouncer.flush()
    debouncer.flush()

    assert len(alerts) == MAX_ALERTS_PER_HOUR
