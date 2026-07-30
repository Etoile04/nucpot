"""KR-COMPANY-3 coverage aggregator (NFM-2042).

Reads the append-only deploy-event JSONL produced by ``scripts/lib/deploy_event.sh``
and emits the metric for the company-level KR:

    KR-COMPANY-3 — Deployment Success Rate — target >= 0.90

The metric is the share of events whose ``first_pass_success`` flag is the
literal JSON ``true``. The staging writer records rollback outcomes and keeps
``skip_flag_used`` as the reserved ``false`` schema field (ADR-KR3-A1 C5), so
this file is dumb about those distinctions and simply trusts the writer.

Critical property (acceptance criterion 4): when the JSONL is absent or empty
the value is ``None`` and ``n == 0``. A bare ``ZeroDivisionError`` or a
fabricated 1.0 would both be a silent corruption of the metric the team is
being measured against.

Usage:
    python scripts/okr/coverage_kr3.py                  # default path, staging
    python scripts/okr/coverage_kr3.py --path events.jsonl
    python scripts/okr/coverage_kr3.py --since 2026-07-01 --until 2026-07-30
    python scripts/okr/coverage_kr3.py --environment production
    NFMD_DEPLOY_EVENTS_PATH=/var/log/nfmd.jsonl python scripts/okr/coverage_kr3.py

From ADR-KR3-A1 C6.1 the JSONL carries both staging and production events, so
``--environment`` selects one series. It defaults to ``staging``, which keeps
every pre-C6.1 caller on the unchanged v1 baseline.

Environment variables:
    NFMD_DEPLOY_EVENTS_PATH  — host-absolute path to the JSONL; falls back
                               to <repo>/docker/.deploy-events.jsonl.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

# The single source of truth for the threshold. NFM-2042 deliverable 4
# requires this to appear in the report; NFM-2035 spec section 4 criterion 1
# fixes the value.
KR3_TARGET: float = 0.90

# ADR-KR3-A1 §C6.1.4. ``staging`` is first because it is the default: the v1
# baseline (NFM-2039) is the staging series, and once the C6.1 collector starts
# appending prod events into the same JSONL, an unfiltered read would silently
# conflate the two streams and move that baseline.
ENVIRONMENTS: tuple[str, ...] = ("staging", "production")
DEFAULT_ENVIRONMENT: str = ENVIRONMENTS[0]


_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_EVENTS_PATH = _REPO_ROOT / "docker" / ".deploy-events.jsonl"


# ---------------------------------------------------------------------------
# IO
# ---------------------------------------------------------------------------

def _resolve_path(path_arg: str | os.PathLike[str] | None) -> Path:
    if path_arg is not None:
        return Path(path_arg)
    env = os.environ.get("NFMD_DEPLOY_EVENTS_PATH")
    if env:
        return Path(env)
    return _DEFAULT_EVENTS_PATH


def load_events(path: Path) -> list[dict[str, Any]]:
    """Read a JSONL of deploy events, tolerating missing/blank/malformed lines.

    Lines that fail to parse or that are not JSON objects missing the
    ``first_pass_success`` field are skipped. The writer is required to emit
    schema-complete objects, so any skip is a real problem in the upstream
    pipeline; this loader stays quiet by design so a single malformed line
    does not block a weekly KR report.
    """
    if not path.exists():
        return []
    try:
        text = path.read_text()
    except OSError:
        return []

    events: list[dict[str, Any]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        if "first_pass_success" not in obj:
            continue
        events.append(obj)
    return events


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def filter_window(
    events: list[dict[str, Any]],
    since: str | None,
    until: str | None,
) -> list[dict[str, Any]]:
    """Filter to events whose ``ts`` falls inside ``[since, until]``.

    Both bounds are inclusive. ``since`` / ``until`` are YYYY-MM-DD strings;
    ``until`` covers the whole named day. When a bound is ``None`` it does
    not constrain that side. Events with unparseable ``ts`` are dropped when
    a bound is set (their date cannot be judged) and kept when both bounds
    are None (we have no reason to throw them out).
    """
    if since is None and until is None:
        return list(events)

    since_d = datetime.strptime(since, "%Y-%m-%d").date() if since else None
    until_d = datetime.strptime(until, "%Y-%m-%d").date() if until else None

    out: list[dict[str, Any]] = []
    for ev in events:
        ts = str(ev.get("ts", ""))
        try:
            d = datetime.strptime(ts[:10], "%Y-%m-%d").date()
        except ValueError:
            continue
        if since_d is not None and d < since_d:
            continue
        if until_d is not None and d > until_d:
            continue
        out.append(ev)
    return out


def compute_value(events: Iterable[dict[str, Any]]) -> float | None:
    """Compute first-pass success rate.

    Returns ``None`` when the input is empty — a metric that is incapable of
    distinguishing "no data yet" from "100% success" is a metric that
    incentivises silence, which is exactly what KR-3 exists to prevent.
    """
    total = 0
    success = 0
    for ev in events:
        total += 1
        # ``is True`` is strict on purpose: a string "true" or a non-bool
        # truthy value is not a real first-pass success.
        if ev.get("first_pass_success") is True:
            success += 1
    if total == 0:
        return None
    return success / total


def filter_environment(
    events: list[dict[str, Any]],
    environment: str,
) -> list[dict[str, Any]]:
    """Keep only events emitted by ``environment`` (ADR-KR3-A1 §C6.1.4).

    Applied after :func:`load_events` — never at IO — so a mixed staging +
    production JSONL is read whole and then split. Filtering at the reader
    would make ``n`` unrecoverable for the other stream.

    The match is exact. An event whose ``environment`` is missing or is some
    third value cannot be attributed to either series, so it is dropped rather
    than folded into the default one; a mis-attributed event moves a KR the
    company is measured against.
    """
    return [ev for ev in events if ev.get("environment") == environment]


def build_report(
    path: Path,
    since: str | None,
    until: str | None,
    environment: str = DEFAULT_ENVIRONMENT,
) -> dict[str, Any]:
    """Assemble the JSON report consumed by the 5-KR aggregator (NFM-2041).

    ``environment`` defaults to staging so a caller that predates C6.1 keeps
    reading the unchanged v1 baseline. The report shape is deliberately not
    extended with the environment: §C6.1.4 requires the staging payload to
    stay byte-for-byte identical to the pre-change run.
    """
    raw = load_events(path)
    in_env = filter_environment(raw, environment)
    windowed = filter_window(in_env, since, until)
    value = compute_value(windowed)
    return {
        "value": value,
        "target": KR3_TARGET,
        "n": len(windowed),
        "computed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source_window": {"since": since, "until": until},
    }



# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _validate_date(value: str) -> str:
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"must be YYYY-MM-DD, got: {value}"
        ) from exc
    return value


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compute KR-COMPANY-3 (Deployment Success Rate) from the "
            "append-only deploy-event JSONL."
        ),
    )
    parser.add_argument(
        "--path",
        default=None,
        help="Path to the deploy-event JSONL. "
        "Defaults to $NFMD_DEPLOY_EVENTS_PATH or <repo>/docker/.deploy-events.jsonl.",
    )
    parser.add_argument(
        "--since",
        default=None,
        type=_validate_date,
        help="Inclusive lower bound (YYYY-MM-DD).",
    )
    parser.add_argument(
        "--until",
        default=None,
        type=_validate_date,
        help="Inclusive upper bound (YYYY-MM-DD, full day).",
    )
    parser.add_argument(
        "--environment",
        default=DEFAULT_ENVIRONMENT,
        choices=ENVIRONMENTS,
        help="Deploy environment to report on. Default: %(default)s "
        "(the v1 KR-3 baseline). The JSONL is a mixed stream from C6.1 onward, "
        "so this selects one series without disturbing the other.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    report = build_report(
        _resolve_path(args.path),
        args.since,
        args.until,
        environment=args.environment,
    )
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
