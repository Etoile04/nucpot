"""Python mirror of ``scripts/lib/deploy_event.sh`` for the production
durable producer (NFM-2110, ADR-KR3-A1 C6.1.1).

The bash emitter sources from ``scripts/lib/deploy_event.sh`` on the
self-hosted runner, but the production durable producer runs on a hosted
``ubuntu-latest`` runner where sourcing the bash lib does not work. The
producer therefore assembles the §3.1 deploy-event object in Python and
emits it as JSON to stdout; the workflow captures stdout and uploads it as
a GHA artifact named ``nfm-deploy-event-<run_id>-<attempt>.json``.

This module deliberately does NOT touch the JSONL file — the collector
workflow owns writes per ADR-KR3-A1 C6.1.1 stage 2.

Field names and ordering mirror the bash lib so the schema is single-source
from both the staging producer's and the prod producer's perspective.
If you change a key here, change ``deploy_event.sh`` too.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# §3.1 schema — the canonical field list for the Python emitter.
# ---------------------------------------------------------------------------

SCHEMA_FIELDS: tuple[str, ...] = (
    "event_id",
    "ts",
    "environment",
    "triggered_by",
    "commit_sha",
    "first_pass_success",
    "health_gate_first_poll_passed",
    "rollback_triggered",
    "skip_flag_used",
    "duration_ms",
)

# Repo layout mirrors ``scripts/lib/deploy_event.sh``: docker/.deploy-events.jsonl
# sits beside docker/.staging-deploy-state. The Python emitter is a producer
# only — it reads ``NFMD_DEPLOY_EVENTS_PATH`` to validate the deployment
# context, but does NOT open or write to the file.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_EVENTS_PATH = _REPO_ROOT / "docker" / ".deploy-events.jsonl"

# Mirror bash ``_deploy_event_bool``: only these strings coerce to true.
_BASH_TRUTHY = frozenset({"true", "True", "TRUE", "yes", "YES"})


def resolve_events_path() -> Path:
    """Return ``NFMD_DEPLOY_EVENTS_PATH`` if set, else the repo fallback.

    Mirrors ``scripts/lib/deploy_event.sh::deploy_event_path()``. Used by
    the Python emitter only to log/validate the configured persistence
    path; the emitter does not open or write to this file (the collector
    workflow owns writes per C6.1.1).
    """
    env = os.environ.get("NFMD_DEPLOY_EVENTS_PATH")
    return Path(env) if env else _DEFAULT_EVENTS_PATH


def now_iso_utc() -> str:
    """Current UTC time formatted ``YYYY-MM-DDTHH:MM:SSZ``.

    Matches the bash emitter's ``date -u +%Y-%m-%dT%H:%M:%SZ`` so the two
    producers are byte-for-byte interchangeable on this field.
    """
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def bool_literal(value: Any) -> bool:
    """Mirror bash ``_deploy_event_bool``: only recognised truth values
    become true; anything else is false.

    A metric that measures the team must not be gameable by a malformed
    flag silently reading as success. Accepts Python ``bool`` and the
    bash truthy-string set.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        # ``int`` is a parent of ``bool``; the bool branch above already
        # handled True/False. Treat other ints as truthy iff non-zero.
        return value != 0
    if isinstance(value, str):
        return value in _BASH_TRUTHY or value == "1"
    return False


def int_literal(value: Any) -> int:
    """Mirror bash ``_deploy_event_int``: non-negative JSON integer; else 0."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return max(value, 0)
    if isinstance(value, str):
        try:
            return max(int(value), 0)
        except ValueError:
            return 0
    return 0


def build_event(
    *,
    environment: str,
    triggered_by: str,
    commit_sha: str,
    first_pass_success: Any,
    health_gate_first_poll_passed: Any,
    rollback_triggered: Any,
    skip_flag_used: Any,
    duration_ms: Any,
) -> dict[str, Any]:
    """Assemble a §3.1 schema-conformant event dict.

    Field names are exactly the ``SCHEMA_FIELDS`` list above; ``json.dumps``
    emits Python ``True``/``False`` as lowercase ``true``/``false`` so the
    JSON payload matches the bash emitter byte-for-byte on booleans.

    Boolean and integer arguments are coerced through ``bool_literal`` and
    ``int_literal`` so the producer can pass either Python natives (when
    invoked in-process) or the strings GHA ``${{ needs.* }}`` substitutes
    (when invoked via the CLI).
    """
    return {
        "event_id": str(uuid.uuid4()),
        "ts": now_iso_utc(),
        "environment": environment,
        "triggered_by": triggered_by,
        "commit_sha": commit_sha,
        "first_pass_success": bool_literal(first_pass_success),
        "health_gate_first_poll_passed": bool_literal(health_gate_first_poll_passed),
        "rollback_triggered": bool_literal(rollback_triggered),
        "skip_flag_used": bool_literal(skip_flag_used),
        "duration_ms": int_literal(duration_ms),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Emit a §3.1 deploy-event JSON object to stdout. The producer "
            "workflow captures stdout and uploads it as a GHA artifact; the "
            "collector workflow owns the JSONL write."
        ),
    )
    parser.add_argument("--environment", required=True, help="Environment name (e.g. 'production').")
    parser.add_argument("--triggered-by", required=True, help="GitHub actor or cron identifier.")
    parser.add_argument("--commit-sha", required=True, help="Full or short commit SHA.")
    parser.add_argument(
        "--first-pass-success",
        required=True,
        help="'true' or 'false' (mapped from ${{ needs.smoke-test.result }}).",
    )
    parser.add_argument(
        "--health-gate-first-poll-passed",
        required=True,
        help="'true' or 'false'. Prod always emits false (C6.1.3).",
    )
    parser.add_argument(
        "--rollback-triggered",
        required=True,
        help="'true' or 'false'. Prod always emits false (C6.1.3).",
    )
    parser.add_argument(
        "--skip-flag-used",
        required=True,
        help="'true' or 'false'. Reserved literal false (ADR-KR3-A1 C5).",
    )
    parser.add_argument(
        "--duration-ms",
        required=True,
        help="Deploy duration in milliseconds (non-negative integer).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Build a §3.1 deploy event and emit it as JSON to stdout.

    This program does NOT write to ``NFMD_DEPLOY_EVENTS_PATH`` — it only
    reads the env var (via ``resolve_events_path``) to validate the
    deployment context. The producer's only persistent output is the GHA
    artifact (uploaded by the calling workflow).
    """
    args = _parse_args(argv)
    event = build_event(
        environment=args.environment,
        triggered_by=args.triggered_by,
        commit_sha=args.commit_sha,
        first_pass_success=args.first_pass_success,
        health_gate_first_poll_passed=args.health_gate_first_poll_passed,
        rollback_triggered=args.rollback_triggered,
        skip_flag_used=args.skip_flag_used,
        duration_ms=args.duration_ms,
    )
    json.dump(event, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
