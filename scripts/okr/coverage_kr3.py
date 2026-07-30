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
    python scripts/okr/coverage_kr3.py                  # default: all environments
    python scripts/okr/coverage_kr3.py --path events.jsonl
    python scripts/okr/coverage_kr3.py --since 2026-07-01 --until 2026-07-30
    python scripts/okr/coverage_kr3.py --environment staging
    python scripts/okr/coverage_kr3.py --environment production
    NFMD_DEPLOY_EVENTS_PATH=/var/log/nfmd.jsonl python scripts/okr/coverage_kr3.py

From ADR-KR3-A2 §Consequences, the default filter is ``all`` — that is the
backward-compatible choice because pre-C6.1 callers did not pass
``--environment`` and read the whole JSONL. Tying the default to a single
series would silently move the v1 baseline once the prod collector started
appending to the same file. The ``staging`` and ``production`` choices are
explicit filter modes for callers that need to isolate one series.

Environment variables:
    NFMD_DEPLOY_EVENTS_PATH  — host-absolute path to the staging-series JSONL;
                               falls back to <repo>/docker/.deploy-events.jsonl.
    NFMD_PROD_EVENTS_PATH    — host-absolute path to the production-series
                               JSONL (the file the prod collector appends to);
                               falls back to
                               /Users/lwj04/.nfmd/master-deploy-events.jsonl
                               on the Mac Studio. Read only when ``--environment``
                               is ``all`` or ``production``.
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

# ADR-KR3-A2 §Consequences: the default filter is "all environments" so the
# v1 baseline is preserved (today's behaviour is to read the whole JSONL).
# ``staging`` and ``production`` are the explicit single-series filters.
ENVIRONMENTS: tuple[str, ...] = ("all", "staging", "production")
DEFAULT_ENVIRONMENT: str = ENVIRONMENTS[0]


_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_EVENTS_PATH = _REPO_ROOT / "docker" / ".deploy-events.jsonl"
_DEFAULT_PROD_PATH = Path("/Users/lwj04/.nfmd/master-deploy-events.jsonl")


# ---------------------------------------------------------------------------
# IO
# ---------------------------------------------------------------------------

def _resolve_prod_path(path_arg: str | os.PathLike[str] | None) -> Path:
    """Resolve the production-series JSONL path.

    Lookup order: explicit ``--prod-path`` arg, then ``NFMD_PROD_EVENTS_PATH``
    env var, then the host-absolute fallback the collector writes to on the
    Mac Studio. The default is intentionally host-absolute (not repo-relative)
    because the prod collector runs as a self-hosted step that writes to a
    fixed location on the Mac Studio, regardless of the runner's checkout.
    """
    if path_arg is not None:
        return Path(path_arg)
    env = os.environ.get("NFMD_PROD_EVENTS_PATH")
    if env:
        return Path(env)
    return _DEFAULT_PROD_PATH


def _resolve_path(path_arg: str | os.PathLike[str] | None) -> Path:
    if path_arg is not None:
        return Path(path_arg)
    env = os.environ.get("NFMD_DEPLOY_EVENTS_PATH")
    if env:
        return Path(env)
    return _DEFAULT_EVENTS_PATH


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

    The match is exact. ``environment="all"`` is a no-op (each event is
    attributable to itself, so an "all" filter is the identity). An event
    whose ``environment`` is missing or is some third value cannot be
    attributed to either series, so it is dropped rather than folded into
    the default one; a mis-attributed event moves a KR the company is
    measured against.
    """
    if environment == "all":
        return list(events)
    return [ev for ev in events if ev.get("environment") == environment]


def build_report(
    path: Path,
    since: str | None,
    until: str | None,
    environment: str = DEFAULT_ENVIRONMENT,
    prod_path: Path | None = None,
) -> dict[str, Any]:
    """Assemble the JSON report consumed by the 5-KR aggregator (NFM-2041).

    ``environment`` defaults to ``all`` so a pre-C6.1 caller that does not
    pass ``--environment`` reads the whole JSONL — same as today's behaviour
    (ADR-KR3-A2 §Consequences). When ``environment`` is ``all`` or
    ``production``, the production-series JSONL is also read (from
    ``prod_path`` if provided, otherwise the resolved default); the two
    streams are merged before filtering.

    The report shape is deliberately not extended with the environment:
    §C6.1.4 requires the staging payload to stay byte-for-byte identical to
    the pre-change run, and ``all`` is the default that preserves that.
    """
    if environment == "staging":
        raw = load_events(path)
    else:
        # ``all`` or ``production`` — read both streams and merge. ``all``
        # also reads both so the post-filter union is the full set; otherwise
        # the filter would discard the staging half when only ``production``
        # is requested.
        merged: list[dict[str, Any]] = list(load_events(path))
        if prod_path is not None:
            merged.extend(load_events(prod_path))
        raw = merged

    in_env = filter_environment(raw, environment)
    windowed = filter_window(in_env, since, until)
    value = compute_value(windowed)
    return {
        "value": value,
        "target": KR3_TARGET,
        "n": len(windowed),
        "computed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source_window": {"since": since, "until": until},
        "environment": environment,
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
        "(the v1 KR-3 baseline — reads staging + production JSONLs together, "
        "matching pre-C6.1 behaviour). Use 'staging' or 'production' to "
        "isolate one series without disturbing the other.",
    )
    parser.add_argument(
        "--prod-path",
        default=None,
        help="Path to the production-series JSONL (the file the prod "
        "collector appends to). Defaults to $NFMD_PROD_EVENTS_PATH or "
        "/Users/lwj04/.nfmd/master-deploy-events.jsonl. Ignored when "
        "--environment is 'staging'.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    prod_path = _resolve_prod_path(args.prod_path) if args.environment != "staging" else None
    report = build_report(
        _resolve_path(args.path),
        args.since,
        args.until,
        environment=args.environment,
        prod_path=prod_path,
    )
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
