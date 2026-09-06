#!/usr/bin/env python3
"""
Stale-checkout watchdog for Paperclip.

Scans all issues in a Paperclip company for checkouts that have been idle
longer than a configurable threshold (default 30 minutes) and reaps them
by POSTing a release + comment. Safe to run from cron.

Exit codes:
    0 — success (no stale checkouts, or all reaped)
    1 — error (API failure, misconfiguration)
    2 — dry-run found stale checkouts (no mutations)

Environment variables:
    PAPERCLIP_API_URL       — Base URL of the Paperclip API
    PAPERCLIP_API_KEY       — Bearer token for authentication
    PAPERCLIP_COMPANY_ID    — Company UUID for the issues collection

Usage:
    python stale_checkout_watchdog.py [--dry-run] [--stale-threshold-minutes 30] [--verbose]
"""

# ruff: noqa: RUF059

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timedelta
try:  # py3.11+; fallback for the py3.9 CommandLineTools interpreter on the runner
    from datetime import UTC
except ImportError:  # pragma: no cover
    from datetime import timezone as _tz
    UTC = _tz.utc
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

__version__ = "1.0.0"

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Exit-code constants
# ---------------------------------------------------------------------------
EXIT_SUCCESS = 0
EXIT_ERROR = 1
EXIT_DRY_RUN_STALE = 2

# ---------------------------------------------------------------------------
# HTTP client (stdlib-only)
# ---------------------------------------------------------------------------


class HttpClient:
    """Thin wrapper around urllib for Paperclip API calls."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout: int = 30,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _url(self, path: str, query: dict[str, Any] | None = None) -> str:
        url = f"{self._base_url}{path}"
        if query:
            url += "?" + urlencode(query)
        return url

    def _request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
    ) -> Any:
        url = self._url(path, query)
        headers = self._headers()
        data = json.dumps(body).encode("utf-8") if body else None
        req = Request(url, data=data, headers=headers, method=method)
        try:
            with urlopen(req, timeout=self._timeout) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else None
        except HTTPError as exc:
            if exc.code == 403:
                raise PermissionError(f"403 — {exc.reason}") from exc
            raise RuntimeError(f"HTTP {exc.code} {exc.reason} on {method} {path}") from exc

    def get(self, path: str, query: dict[str, Any] | None = None) -> Any:
        return self._request("GET", path, query=query)

    def post(
        self,
        path: str,
        body: dict[str, Any] | None = None,
    ) -> Any:
        return self._request("POST", path, body=body)


# ---------------------------------------------------------------------------
# Pure logic (testable without network)
# ---------------------------------------------------------------------------


def is_stale(updated_at: str, threshold_minutes: int) -> bool:
    """Return True if *updated_at* ISO timestamp is older than the threshold."""
    try:
        ts = datetime.fromisoformat(updated_at)
    except (ValueError, TypeError):
        logger.warning("Could not parse updatedAt=%r, treating as stale", updated_at)
        return True
    now = datetime.now(UTC)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    cutoff = now - timedelta(minutes=threshold_minutes)
    return ts < cutoff


def parse_issues_response(data: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalise raw API response into a list of issue dicts."""
    return [
        {
            "id": item.get("id", ""),
            "identifier": item.get("identifier", ""),
            "status": item.get("status", ""),
            "updatedAt": item.get("updatedAt", ""),
            "checkoutRunId": item.get("checkoutRunId"),
        }
        for item in (data or [])
    ]


def classify_issues(
    issues: list[dict[str, Any]],
    threshold_minutes: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split issues into (stale_checkouts, active).

    An issue is *stale* when it has a non-empty ``checkoutRunId`` and its
    ``updatedAt`` is older than the threshold.
    """
    stale: list[dict[str, Any]] = []
    active: list[dict[str, Any]] = []
    for issue in issues:
        run_id = issue.get("checkoutRunId")
        if run_id and is_stale(issue.get("updatedAt", ""), threshold_minutes):
            stale.append(issue)
        else:
            active.append(issue)
    return stale, active


def build_reap_comment(issue_id: str, run_id: str) -> str:
    """Return the comment body for a reaped stale checkout."""
    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return (
        f"[STALE-CHECKOUT-REAPED] Issue {issue_id} had a stale checkout "
        f"(checkoutRunId={run_id}). "
        f"Automatically released by the stale-checkout watchdog at {ts}."
    )


# ---------------------------------------------------------------------------
# API interactions
# ---------------------------------------------------------------------------


def fetch_all_issues(
    api: HttpClient,
    company_id: str,
    page_size: int = 100,
) -> list[dict[str, Any]]:
    """Paginate through all issues for the company."""
    all_issues: list[dict[str, Any]] = []
    offset = 0
    while True:
        path = f"/api/companies/{company_id}/issues"
        data = api.get(path, query={"limit": page_size, "offset": offset})
        page = parse_issues_response(data)
        logger.debug("Fetched page offset=%d: %d issues", offset, len(page))
        if not page:
            break
        all_issues.extend(page)
        if len(page) < page_size:
            break
        offset += page_size
    return all_issues


def reap_stale_issue(
    api: HttpClient,
    issue: dict[str, Any],
) -> bool:
    """Release a stale checkout and post a comment. Returns True on success."""
    issue_id = issue.get("id", "")
    run_id = issue.get("checkoutRunId", "")
    identifier = issue.get("identifier", issue_id)

    # 1. POST /api/issues/{id}/release
    try:
        api.post(f"/api/issues/{issue_id}/release")
    except PermissionError:
        logger.info(
            "[SKIP] %s — 403 on release (already handled by another agent)",
            identifier,
        )
        return False

    # 2. POST /api/issues/{id}/comments  body: {"body": "..."}
    comment_body = build_reap_comment(identifier, run_id)
    api.post(f"/api/issues/{issue_id}/comments", body={"body": comment_body})
    logger.info("[REAPED] %s (run=%s)", identifier, run_id)
    return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(
    args: argparse.Namespace | None = None,
    api_client: HttpClient | None = None,
    company_id: str | None = None,
) -> int:
    """Run the watchdog. Returns an exit code."""
    if args is None:
        args = parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    # Resolve configuration from environment
    base_url = os.environ.get("PAPERCLIP_API_URL", "").rstrip("/")
    api_key = os.environ.get("PAPERCLIP_API_KEY", "")
    cid = company_id or os.environ.get("PAPERCLIP_COMPANY_ID", "")

    if not base_url or not api_key or not cid:
        logger.error(
            "Missing required env vars: PAPERCLIP_API_URL, PAPERCLIP_API_KEY, PAPERCLIP_COMPANY_ID"
        )
        return EXIT_ERROR

    api = api_client or HttpClient(base_url, api_key)
    threshold = args.stale_threshold_minutes
    dry_run = args.dry_run

    try:
        issues = fetch_all_issues(api, cid)
    except Exception as exc:
        logger.error("Failed to fetch issues: %s", exc)
        return EXIT_ERROR

    logger.info("Scanned %d total issues (threshold=%d min)", len(issues), threshold)

    stale, active = classify_issues(issues, threshold)

    if not stale:
        logger.info("No stale checkouts found.")
        return EXIT_SUCCESS

    logger.info("Found %d stale checkout(s).", len(stale))
    for issue in stale:
        identifier = issue.get("identifier", issue.get("id"))
        run_id = issue.get("checkoutRunId", "?")
        updated = issue.get("updatedAt", "?")
        logger.info("  STALE: %s  run=%s  updatedAt=%s", identifier, run_id, updated)

    if dry_run:
        logger.info("[DRY RUN] Would reap %d stale checkout(s).", len(stale))
        return EXIT_DRY_RUN_STALE

    # Live mode — reap each stale checkout
    reaped = 0
    for issue in stale:
        try:
            if reap_stale_issue(api, issue):
                reaped += 1
        except Exception as exc:
            logger.error(
                "Failed to reap %s: %s",
                issue.get("identifier", issue.get("id")),
                exc,
            )

    logger.info("Reaped %d/%d stale checkout(s).", reaped, len(stale))
    return EXIT_SUCCESS


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Reap stale Paperclip issue checkouts.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Environment variables:\n"
            "  PAPERCLIP_API_URL        Base URL of the Paperclip API\n"
            "  PAPERCLIP_API_KEY        Bearer token for authentication\n"
            "  PAPERCLIP_COMPANY_ID     Company UUID\n"
            "\n"
            "Exit codes:\n"
            "  0  Success (no stale checkouts or all reaped)\n"
            "  1  Error (API failure, misconfiguration)\n"
            "  2  Dry-run found stale checkouts (no mutations)\n"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Log stale checkouts without releasing them",
    )
    parser.add_argument(
        "--stale-threshold-minutes",
        type=int,
        default=30,
        help="Minutes after which a checkout is considered stale (default: 30)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        default=False,
        help="Enable DEBUG-level logging",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    sys.exit(main())
