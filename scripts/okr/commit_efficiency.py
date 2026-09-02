"""Commit efficiency & structural waste rate calculator.

Extracts git log data for a given period, resolves Paperclip issue
statuses via the REST API, and outputs a structured JSON report.

Metrics (as defined in NFM-434 spec):
    commitEfficiency     = completed-issues / total-commits
    structuralWasteRate = commits-without-issue-ref / total-commits

Usage:
    python scripts/okr/commit_efficiency.py --since 2026-06-23 --until 2026-06-29
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
from datetime import datetime
from typing import Any

from scripts.okr import PaperclipEmptyResultError, fetch_all_issues

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Git log parsing
# ---------------------------------------------------------------------------

_ISSUE_REF_PATTERN = re.compile(r"NFM-\d+")

# Revision basis for the KR-2 measurement (NFM-2204 / R2).
#
# The shared remote branch, not the local checkout: the number must describe
# merged history, not whatever the measuring workspace happens to be on. Fetch
# before measuring — a stale local ref reproduces the very defect this pins.
DEFAULT_REV = "origin/main"

# Merge commits carry no authored subject and are exempt from the
# commit-reference gate by construction (parent-count >= 2). Excluding them
# keeps this metric counting the same population the gate polices.
_NON_MERGE_FILTER = "--max-parents=1"


def parse_git_log(git_output: str) -> list[dict[str, str]]:
    """Parse raw ``git log --oneline`` output into structured commit records.

    Each non-empty line becomes a dict with ``hash`` and ``message`` keys.
    """
    commits: list[dict[str, str]] = []
    for line in git_output.strip().splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split(maxsplit=1)
        commits.append(
            {
                "hash": parts[0],
                "message": parts[1] if len(parts) > 1 else "",
            }
        )
    return commits


# ---------------------------------------------------------------------------
# Issue reference extraction
# ---------------------------------------------------------------------------


def extract_issue_refs(commit_message: str) -> list[str]:
    """Extract unique ``NFM-XXX`` references from a commit message."""
    matches = _ISSUE_REF_PATTERN.findall(commit_message)
    seen: set[str] = set()
    unique: list[str] = []
    for ref in matches:
        if ref not in seen:
            seen.add(ref)
            unique.append(ref)
    return unique


# ---------------------------------------------------------------------------
# Enrich commits with issue refs
# ---------------------------------------------------------------------------


def enrich_commits_with_refs(commits: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Return new commit dicts with an ``issue_refs`` field added.

    Does NOT mutate the input list or its items.
    """
    enriched: list[dict[str, Any]] = []
    for commit in commits:
        refs = extract_issue_refs(commit["message"])
        enriched.append(
            {
                **commit,
                "issue_refs": refs,
            }
        )
    return enriched


# ---------------------------------------------------------------------------
# Paperclip API integration
# ---------------------------------------------------------------------------


def fetch_issue_statuses(
    issue_refs: list[str],
    api_url: str,
    company_id: str,
    api_key: str | None = None,
) -> dict[str, str]:
    """Query the Paperclip API for issue statuses using paginated fetch.

    Fetches ALL issues via ``fetch_all_issues`` then filters locally for
    the requested ``issue_refs``. This fixes the silent 1000-row cap
    undercount (288-vs-713 NFMDP).

    Returns a mapping of issue identifier → status string.

    Raises:
        PaperclipFetchError: propagated from ``fetch_all_issues`` so the
            downstream report can degrade to ``[no data]``. The previous
            behaviour of silently defaulting every ref to ``"unknown"``
            was the NFM-2443 defect — a fetch failure rendered as
            ``commitEfficiency = 0.000`` rather than an honest empty
            measurement.
    """
    statuses: dict[str, str] = {}
    if not issue_refs:
        return statuses

    # fetch_all_issues raises PaperclipFetchError on auth/HTTP/parse
    # failure; we deliberately do NOT swallow it (NFM-2443 AC1+AC3).
    all_issues = fetch_all_issues(api_url, company_id, {}, api_key=api_key)
    ref_set = set(issue_refs)

    # AC1 (NFM-2492): an empty issue list with a non-empty ref set is not a
    # measurement. The fetch succeeded but returned nothing — the same
    # reader-cannot-distinguish failure that NFM-2443 closed for the
    # raised-exception path, reached here by an HTTP 200 with ``[]``.
    # A genuinely empty commit range (no refs at all) returns above, so
    # this guard is only reached when there *is* something to look up.
    if not all_issues:
        raise PaperclipEmptyResultError(
            f"fetch_issue_statuses: Paperclip returned 0 issues for company "
            f"'{company_id}' but {len(ref_set)} NFM ref(s) were expected "
            f"({sorted(ref_set)}); the lookup cannot have matched anything. "
            "This usually means a wrong company_id, a token scoped to no "
            "issues, or a commit range referencing issues from another "
            "project (see NFM-2492)."
        )

    for issue in all_issues:
        identifier = issue.get("identifier", "")
        if identifier in ref_set:
            statuses[identifier] = issue.get("status", "unknown")

    # AC2 (NFM-2492): a non-empty list against which *zero* refs resolve is
    # the lookup-mismatch class — e.g. NFM-2446's `key`-vs-`identifier` bug,
    # where every issue carried a `key` but the caller keys on `identifier`.
    # 2438 issues come back and not one ref matches: the metric would still
    # compute `0.000` and the reader could not tell that from a real zero.
    if not statuses:
        raise PaperclipEmptyResultError(
            f"fetch_issue_statuses: Paperclip returned {len(all_issues)} "
            f"issue(s) but none resolved to the {len(ref_set)} NFM ref(s) "
            f"expected ({sorted(ref_set)}); this is a lookup mismatch, not a "
            "measurement. Likely the caller is keying on a field the API "
            "no longer populates (see NFM-2446 / NFM-2492)."
        )

    # Any ref not found in the full issue list gets "unknown" — a real
    # signal about those individual issues, not a measurement failure.
    for ref in issue_refs:
        if ref not in statuses:
            statuses[ref] = "unknown"

    return statuses


# ---------------------------------------------------------------------------
# Metrics calculation
# ---------------------------------------------------------------------------

_COMPLETED_STATUSES = frozenset({"done", "closed"})
_IN_PROGRESS_STATUSES = frozenset({"in_progress", "in progress"})


def calculate_metrics(
    commits: list[dict[str, Any]],
    statuses: dict[str, str],
) -> dict[str, Any]:
    """Compute commit efficiency and structural waste rate.

    Formulas (per NFM-434 spec):
        commitEfficiency     = completed-issues / total-commits
        structuralWasteRate = commits-without-issue-ref / total-commits

    Note: commitEfficiency intentionally mixes issue-count (numerator)
    with commit-count (denominator) per the CTO-defined formula. This
    measures how many completed issues the team shipped relative to
    total commit volume — a productivity density metric.
    """
    total = len(commits)
    if total == 0:
        return {
            "commits": {"total": 0, "withIssueRef": 0, "withoutIssueRef": 0},
            "issues": {"referenced": 0, "completed": 0, "inProgress": 0, "other": 0},
            "metrics": {"commitEfficiency": 0.0, "structuralWasteRate": 0.0},
        }

    with_ref = 0
    without_ref = 0
    all_refs: set[str] = set()

    for commit in commits:
        refs = commit.get("issue_refs", [])
        if refs:
            with_ref += 1
            all_refs.update(refs)
        else:
            without_ref += 1

    referenced = len(all_refs)
    completed = 0
    in_progress = 0
    other = 0

    for ref in all_refs:
        status = statuses.get(ref, "unknown").lower().strip()
        if status in _COMPLETED_STATUSES:
            completed += 1
        elif status in _IN_PROGRESS_STATUSES:
            in_progress += 1
        else:
            other += 1

    return {
        "commits": {
            "total": total,
            "withIssueRef": with_ref,
            "withoutIssueRef": without_ref,
        },
        "issues": {
            "referenced": referenced,
            "completed": completed,
            "inProgress": in_progress,
            "other": other,
        },
        "metrics": {
            "commitEfficiency": completed / total,
            "structuralWasteRate": without_ref / total,
        },
    }


# ---------------------------------------------------------------------------
# Report assembly
# ---------------------------------------------------------------------------


def build_report(
    period_start: str,
    period_end: str,
    commits: list[dict[str, Any]],
    statuses: dict[str, str],
) -> dict[str, Any]:
    """Assemble the final JSON-serializable report."""
    metrics = calculate_metrics(commits, statuses)
    return {
        "period": {"start": period_start, "end": period_end},
        **metrics,
    }


# ---------------------------------------------------------------------------
# CLI helpers
# ---------------------------------------------------------------------------

_DATE_FORMAT = "%Y-%m-%d"


def _validate_date(value: str, arg_name: str) -> str:
    """Validate that a date string matches YYYY-MM-DD format."""
    try:
        datetime.strptime(value, _DATE_FORMAT)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"{arg_name} must be {_DATE_FORMAT}, got: {value}"
        ) from exc
    return value


def run_git_log(since: str, until: str, *, rev: str) -> str:
    """Execute ``git log --oneline`` for a date range on an explicit revision.

    ``rev`` is keyword-only and deliberately has no default. Before NFM-2204
    this function passed no revision at all, so git fell back to ``HEAD``: the
    metric measured whichever branch happened to be checked out and moved with
    the workspace rather than with behaviour. Callers must now state which
    history they mean; :func:`build_arg_parser` supplies the CLI default.

    ``--max-parents=1`` drops merge commits. The commit-reference gate exempts
    them by construction (parent-count >= 2), so counting them here would make
    this metric's denominator disagree with what the gate enforces.
    """
    result = subprocess.run(
        [
            "git",
            "log",
            "--oneline",
            _NON_MERGE_FILTER,
            f"--since={since}",
            f"--until={until}",
            rev,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def build_arg_parser() -> argparse.ArgumentParser:
    """Construct the CLI parser.

    Extracted from :func:`main` so the ``--rev`` default is assertable without
    invoking the network-dependent report pipeline.
    """
    parser = argparse.ArgumentParser(
        description="Calculate commit efficiency and structural waste rate.",
    )
    parser.add_argument(
        "--since",
        required=True,
        type=lambda v: _validate_date(v, "--since"),
        help="Period start (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--until",
        required=True,
        type=lambda v: _validate_date(v, "--until"),
        help="Period end (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--rev",
        default=DEFAULT_REV,
        help=(
            "Revision basis to measure (default: %(default)s). Pinning this to "
            "the shared remote branch keeps the number reproducible; leaving it "
            "to the local checkout does not. Fetch first — a stale local ref "
            "silently reproduces the defect this flag exists to fix."
        ),
    )
    parser.add_argument(
        "--api-url",
        default=None,
        help=(
            "Paperclip API base URL. Required — set $PAPERCLIP_API_URL "
            "or pass --api-url explicitly (NFM-2442/NFM-2444)."
        ),
    )
    parser.add_argument(
        "--company-id",
        default=None,
        help=(
            "Company UUID for scoped issue endpoint. Falls back to PAPERCLIP_COMPANY_ID env var."
        ),
    )
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()

    company_id = args.company_id or os.environ.get("PAPERCLIP_COMPANY_ID", "")
    if not company_id:
        parser = build_arg_parser()
        parser.error("--company-id is required (or set PAPERCLIP_COMPANY_ID env var)")

    api_url = args.api_url or os.environ.get("PAPERCLIP_API_URL")
    if not api_url:
        parser = build_arg_parser()
        parser.error(
            "--api-url is required (or set PAPERCLIP_API_URL env var). "
            "The script must not guess a host — a wrong URL silently "
            "produces plausible-looking but false KR values (NFM-2442)."
        )

    raw_log = run_git_log(args.since, args.until, rev=args.rev)
    commits = parse_git_log(raw_log)
    enriched = enrich_commits_with_refs(commits)

    all_refs = list({ref for c in enriched for ref in c["issue_refs"]})
    api_key = os.environ.get("PAPERCLIP_API_KEY", "")
    statuses = fetch_issue_statuses(
        all_refs,
        api_url,
        company_id,
        api_key=api_key,
    )

    report = build_report(args.since, args.until, enriched, statuses)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
