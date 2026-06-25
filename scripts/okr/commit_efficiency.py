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
import re
import subprocess
import urllib.request
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Git log parsing
# ---------------------------------------------------------------------------

_ISSUE_REF_PATTERN = re.compile(r"NFM-\d+")


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
        commits.append({
            "hash": parts[0],
            "message": parts[1] if len(parts) > 1 else "",
        })
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
        enriched.append({
            **commit,
            "issue_refs": refs,
        })
    return enriched


# ---------------------------------------------------------------------------
# Paperclip API integration
# ---------------------------------------------------------------------------

def fetch_issue_statuses(
    issue_refs: list[str],
    api_url: str,
) -> dict[str, str]:
    """Query the Paperclip API for issue statuses.

    Returns a mapping of issue key → status string.
    On failure, logs a warning and the issue gets status ``"unknown"``.
    """
    statuses: dict[str, str] = {}
    if not issue_refs:
        return statuses

    query = " OR ".join(issue_refs)
    try:
        url = f"{api_url}/api/issues?q={urllib.request.quote(query)}"
        with urllib.request.urlopen(url, timeout=10) as resp:
            body = json.loads(resp.read().decode())
        for issue in body.get("issues", []):
            statuses[issue["key"]] = issue.get("status", "unknown")
    except urllib.error.URLError as exc:
        logger.warning("Paperclip API unreachable at %s: %s", api_url, exc)
        for ref in issue_refs:
            statuses[ref] = "unknown"
    except (json.JSONDecodeError, KeyError) as exc:
        logger.warning("Paperclip API returned malformed response: %s", exc)
        for ref in issue_refs:
            statuses[ref] = "unknown"
    except Exception as exc:
        logger.warning("Unexpected error fetching issue statuses: %s", exc)
        for ref in issue_refs:
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


def run_git_log(since: str, until: str) -> str:
    """Execute ``git log --oneline`` for the given date range."""
    result = subprocess.run(
        [
            "git", "log", "--oneline",
            f"--since={since}",
            f"--until={until}",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def main() -> None:
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
        "--api-url",
        default="http://localhost:3000",
        help="Paperclip API base URL",
    )
    args = parser.parse_args()

    raw_log = run_git_log(args.since, args.until)
    commits = parse_git_log(raw_log)
    enriched = enrich_commits_with_refs(commits)

    all_refs = list({
        ref for c in enriched for ref in c["issue_refs"]
    })
    statuses = fetch_issue_statuses(all_refs, args.api_url)

    report = build_report(args.since, args.until, enriched, statuses)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
