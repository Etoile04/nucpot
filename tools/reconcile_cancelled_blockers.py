#!/usr/bin/env python3
"""tools/reconcile_cancelled_blockers.py --dry-run (NFM-3600).

Standalone dry-run script for the §4.3-a reconcile routine. Imports the
routine from ``nfm_db.services.adr009_reconcile_routine`` and runs it in
``dry_run=True`` mode against the current Paperclip issue set.

Prints, in order:

1. Total issues scanned.
2. Total dependents touched.
3. Total UUIDs to remove.
4. Sample (first 10) cleared dependencies with before/after
   ``blockedByIssueIds``.

Does NOT mutate the Paperclip DB and does NOT write audit entries
(``dry_run=True`` short-circuits both). Exit code:

* ``0`` — clean run (flag ON, scan completed, no exceptions).
* ``2`` — feature flag is OFF (intentional no-op; the routine's
  contract says skip the scan rather than mutate).
* ``1`` — routine error (raised exception).

Usage::

    NFM_ADR_009_RECONCILIATION_HOOK_ENABLED=on \\
        python tools/reconcile_cancelled_blockers.py --dry-run

    # explicit env vars override any shell defaults
    PAPERCLIP_API_URL=http://localhost:3101 \\
    PAPERCLIP_BOARD_API_KEY=$PAPERCLIP_BOARD_API_KEY \\
        python tools/reconcile_cancelled_blockers.py --dry-run

    # agent-self JWT also works but returns 403 for cross-agent wake:
    # PAPERCLIP_BOARD_API_KEY is preferred. See NFM-3726.
    PAPERCLIP_API_URL=http://localhost:3101 \\
    PAPERCLIP_API_KEY=$PAPERCLIP_RUN_JWT \\
        python tools/reconcile_cancelled_blockers.py --dry-run

Required credentials (NFM-3727):

* ``PAPERCLIP_BOARD_API_KEY`` — **preferred**.  Board-actor API key
  (e.g. ``name=lobster-coo``) that can wake *any* assignee agent.
  Required for the ADR-009 daily reconcile cron to perform cross-agent
  wakeup; the agent-self JWT returns 403 for other agents.
* ``PAPERCLIP_API_KEY`` — fallback.  Agent-self JWT injected by the
  Paperclip runtime.  Only usable for self-wake scenarios.
* ``PAPERCLIP_API_URL`` — base URL of the Paperclip API server
  (defaults to ``http://paperclip-api:3101`` in production).

References:

* NFM-3600 — this issue.
* NFM-3594 — Sibling 1 (§4.3-a source spec).
* NFM-3586 — Sibling 3 (§4.3-c audit writer + flag).

Safety:

* Read-only against Paperclip (uses ``scripts/paperclip_issue_lookup.py``
  collection endpoint which returns the canonical UUID→status map).
* Read-only against the local audit-log table (no ``write_audit_entry``
  calls because ``dry_run=True`` short-circuits the writer).
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from collections.abc import Iterable
from pathlib import Path
from typing import Any

# Allow running this script from the repo root or from anywhere in the
# repo — ``apps/api/src`` is added to ``sys.path`` so the
# ``nfm_db.services.adr009_reconcile_routine`` import resolves.
_REPO_ROOT = Path(__file__).resolve().parents[1]
_API_SRC = _REPO_ROOT / "apps" / "api" / "src"
if str(_API_SRC) not in sys.path:
    sys.path.insert(0, str(_API_SRC))

# Also expose ``scripts/`` so ``import paperclip_issue_lookup`` works.
_SCRIPTS = _REPO_ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from nfm_db.services.adr009_reconcile_routine import (  # noqa: E402
    IssueLike,
    ReconcileResult,
    reconcile_blocked_by_issue_ids,
)
from nfm_db.services.adr009_flag import (  # noqa: E402
    _FLAG_CACHE,
    is_reconcile_routine_enabled,
)


# Sample size for the dry-run "first N cleared dependencies" output.
# 10 mirrors the §4.3 spec's "sample (first 10)".
DRY_RUN_SAMPLE_SIZE = 10


def _iter_all_paperclip_issues() -> list[dict[str, Any]]:
    """Fetch every Paperclip issue row via the script-level helper.

    Uses ``paperclip_issue_lookup.lookup_issues`` which handles
    pagination internally. Issues with ``status in {"cancelled",
    "done"}`` are still yielded so the status_map covers terminal
    blockers — the routine needs them to recognise which dependents to
    clear.
    """
    from paperclip_issue_lookup import ApiError, Ok, lookup_issues

    # ``lookup_issues`` accepts ``q=``, ``status=``, ``assignee_agent_id=``,
    # ``project_id=``, ``max_pages=`` — there is no ``query=`` kwarg (NFM-3600
    # RE smoke). Pass no filters so the helper returns every issue in the
    # company-scoped collection.
    result = lookup_issues()
    if isinstance(result, ApiError):
        # ``lookup_issues`` raises on auth/wrong-path; an ``ApiError``
        # return means the API itself returned an unparseable payload.
        raise RuntimeError(
            f"paperclip lookup_issues returned ApiError("
            f"http_status={result.http_status}, kind={result.kind}): "
            f"{result.body}"
        )
    if not isinstance(result, Ok):
        # Defensive: ``lookup_issues`` only ever returns ``Ok`` or ``ApiError``
        # in practice, but the Union allows ``NotFound`` too. Anything else
        # is a contract drift and must surface loudly.
        raise RuntimeError(
            f"paperclip lookup_issues returned unexpected "
            f"{type(result).__name__}: {result!r}"
        )
    return list(result.issues)


def _collect_paperclip_dependents() -> tuple[list[IssueLike], dict[uuid.UUID, str]]:
    """Fetch every Paperclip issue and return ``(dependents, status_map)``.

    Re-fetches each issue via the per-identifier ``lookup_issue``
    helper so the ``blockedByIssueIds`` payload is complete (the
    collection endpoint silently strips ``blockedBy`` per the trap-3
    note in ``scripts/paperclip_issue_lookup.py``).

    Returns
    -------
    dependents:
        List of :class:`IssueLike` for every issue that has at least
        one entry in ``blockedByIssueIds``.
    status_map:
        ``{issue_id: status}`` for every issue (used as the
        ``lookup_status`` callback for the routine).
    """
    from paperclip_issue_lookup import Ok, lookup_issue

    issues: list[IssueLike] = []
    status_map: dict[uuid.UUID, str] = {}

    for raw in _iter_all_paperclip_issues():
        ident = raw.get("identifier")
        if ident is None:
            continue

        # Re-fetch the expanded view so blockedByIssueIds is present.
        # ``lookup_issue`` returns ``Ok`` (with ``.issues``), ``NotFound``,
        # or ``ApiError``; ``AuthError``/``WrongPath`` raise. The LE-fix at
        # ``f67ccdf8`` corrected the outer ``_iter_all_paperclip_issues``
        # lookup but missed this inner expansion loop, which still
        # referenced the legacy ``.ok`` / ``.issue`` attributes that
        # ``Ok`` does not carry. RE pre-merge smoke caught the regression:
        # the script crashed with "'Ok' object has no attribute 'ok'" on
        # the very first dependent.
        expanded = lookup_issue(ident)
        if not isinstance(expanded, Ok) or not expanded.issues:
            continue
        issue_dict: dict[str, Any] = expanded.issues[0]

        try:
            issue_id = uuid.UUID(issue_dict["id"])
        except (KeyError, TypeError, ValueError):
            continue
        status = issue_dict.get("status", "unknown")
        status_map[issue_id] = status

        blocked_raw = issue_dict.get("blockedByIssueIds") or []
        blocked_ids: list[uuid.UUID] = []
        for bid in blocked_raw:
            try:
                blocked_ids.append(uuid.UUID(str(bid)))
            except (TypeError, ValueError):
                continue

        if blocked_ids:
            issues.append(
                IssueLike(
                    id=issue_id,
                    identifier=ident,
                    status=status,
                    blocked_by_issue_ids=tuple(blocked_ids),
                )
            )

    return issues, status_map


def _print_dry_run_report(result: ReconcileResult) -> None:
    """Print the §4.3-b dry-run output contract."""
    print("ADR-009 §4.3 dry-run — reconcile cancelled-blocker wedges")
    print("=" * 60)
    print(f"  issues scanned            : {result.scanned}")
    print(f"  dependents touched        : {result.touched}")
    print(f"  UUIDs to remove (total)   : {result.uuids_to_remove}")
    print(f"  audit entries (dry-run=0) : {result.audit_entries_written}")
    if result.skipped_flag_off:
        print()
        print("  NOTE: feature flag is OFF — scan short-circuited, no work done.")
        return
    if result.cleared:
        print()
        print(
            f"  Sample (first "
            f"{min(DRY_RUN_SAMPLE_SIZE, len(result.cleared))} of "
            f"{len(result.cleared)} cleared dependencies):"
        )
        for entry in result.cleared[:DRY_RUN_SAMPLE_SIZE]:
            print(
                f"    - {entry.dependent_identifier:<24s} "
                f"({entry.dependent_id})"
            )
            print(
                f"        closing: {entry.closing_issue_id} "
                f"[{entry.closing_issue_status}]"
            )
            print(f"        before:  {list(entry.before)}")
            print(f"        after:   {list(entry.after)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "ADR-009 §4.3 dry-run — scans the current Paperclip issue "
            "set for cancelled/done blockers and reports what the "
            "reconcile routine would clear without mutating."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="(default) Do not mutate; report only.",
    )
    parser.add_argument(
        "--no-dry-run",
        dest="dry_run",
        action="store_false",
        help=argparse.SUPPRESS,  # hidden: §4.3-b ships dry-run only
    )
    args = parser.parse_args(argv)

    # Reset module-level flag cache so env-var changes since import are
    # honoured. The routine does the same read-through check.
    global _FLAG_CACHE
    _FLAG_CACHE = None
    if not is_reconcile_routine_enabled():
        # Allow operators to invoke the script with the flag OFF and
        # still get a meaningful "skipped_flag_off" report rather than
        # a ValueError from a downstream consumer.
        os.environ.setdefault(
            "NFM_ADR_009_RECONCILIATION_HOOK_ENABLED", "on"
        )
        _FLAG_CACHE = None

    try:
        dependents, status_map = _collect_paperclip_dependents()
    except Exception as exc:  # noqa: BLE001 — top-level boundary
        print(f"error: failed to fetch Paperclip issue set: {exc}", file=sys.stderr)
        return 1

    try:
        result = reconcile_blocked_by_issue_ids(
            dependents,
            lookup_status=status_map.get,
            session=None,
            dry_run=args.dry_run,
        )
    except Exception as exc:  # noqa: BLE001 — top-level boundary
        print(f"error: reconcile routine failed: {exc}", file=sys.stderr)
        return 1

    _print_dry_run_report(result)

    # Flag-OFF is an intentional no-op exit code 2 (per NFM-3600 AC).
    if result.skipped_flag_off:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())