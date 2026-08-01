"""Shared helpers for OKR metric scripts.

Provides a paginated issues API client that exhausts all pages, fixing the
silent 1000-row cap that caused the 288-vs-713 NFMDP undercount.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

# Default page size for the Paperclip issues API.
_DEFAULT_PAGE_SIZE = 1000


def fetch_all_issues(
    api_url: str,
    company_id: str,
    params: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Fetch ALL issues from the Paperclip API, paginating through all pages.

    Args:
        api_url: Paperclip API base URL (e.g. ``http://localhost:3000``).
        company_id: The company UUID to scope the query.
        params: Optional query parameters merged into every request
                (e.g. ``{"status": "done"}``).

    Returns:
        A single flat list of issue dicts accumulated across all pages.
        Returns an empty list on any network/API error.
    """
    merged_params = dict(params) if params else {}
    page_size = int(merged_params.get("limit", str(_DEFAULT_PAGE_SIZE)))
    if "limit" not in merged_params:
        merged_params["limit"] = str(page_size)

    all_issues: list[dict[str, Any]] = []
    offset = 0
    page_num = 0

    while True:
        page_num += 1
        merged_params["offset"] = str(offset)
        qs = urllib.parse.urlencode(merged_params)
        url = f"{api_url}/api/companies/{company_id}/issues?{qs}"

        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                body = json.loads(resp.read().decode())
        except urllib.error.URLError as exc:
            logger.warning(
                "Page %d: API unreachable at %s: %s", page_num, url, exc
            )
            break
        except (json.JSONDecodeError, KeyError) as exc:
            logger.warning(
                "Page %d: malformed response from %s: %s", page_num, url, exc
            )
            break
        except Exception as exc:
            logger.warning(
                "Page %d: unexpected error fetching %s: %s",
                page_num,
                url,
                exc,
            )
            break

        # The API returns the issues array directly (not wrapped in an object).
        if isinstance(body, list):
            page_issues = body
        else:
            page_issues = body.get("issues", body.get("data", []))

        count = len(page_issues)
        logger.info(
            "Page %d: fetched %d issues (offset=%d)", page_num, count, offset
        )

        if count == 0:
            break

        all_issues.extend(page_issues)

        # If we received fewer than the page size, this was the last page.
        if count < page_size:
            break

        offset += page_size

    return all_issues
