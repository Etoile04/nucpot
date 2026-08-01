"""OKR metric utilities — pagination helpers and shared API client functions."""

from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_LIMIT = 1000


def fetch_all_issues(
    api_url: str,
    company_id: str,
    params: dict[str, str],
) -> list[dict[str, Any]]:
    """Fetch ALL issues from the Paperclip API, paginating automatically.

    Endpoint: ``GET /api/companies/{company_id}/issues``
    Pagination: ``offset`` + ``limit`` query parameters.
    Loop increments offset by limit until the response returns an empty list.

    Args:
        api_url: Paperclip API base URL (e.g. ``http://localhost:3000``).
        company_id: Company UUID for scoped issue endpoint.
        params: Additional query parameters merged into every request
            (e.g. ``{"status": "done"}``).

    Returns:
        A flat list of issue dicts accumulated from all pages.
    """
    base_url = f"{api_url}/api/companies/{company_id}/issues"
    accumulated: list[dict[str, Any]] = []
    offset = 0
    page_num = 1

    while True:
        query_params = {
            "offset": str(offset),
            "limit": str(_DEFAULT_LIMIT),
            **params,
        }
        query_string = urllib.parse.urlencode(query_params)
        url = f"{base_url}?{query_string}"

        logger.debug(
            "fetch_all_issues: page %d (offset=%d, limit=%d)",
            page_num, offset, _DEFAULT_LIMIT,
        )

        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                body = json.loads(resp.read().decode())
        except urllib.error.URLError as exc:
            logger.error(
                "fetch_all_issues: HTTP error on page %d (offset=%d): %s",
                page_num, offset, exc,
            )
            break
        except (json.JSONDecodeError, KeyError) as exc:
            logger.error(
                "fetch_all_issues: malformed response on page %d: %s",
                page_num, exc,
            )
            break

        if not body:
            logger.debug(
                "fetch_all_issues: empty page %d (offset=%d) — done",
                page_num, offset,
            )
            break

        # Extend accumulated list (new list, never mutate existing items)
        accumulated = [*accumulated, *body]

        logger.info(
            "fetch_all_issues: page %d fetched %d issues (total: %d)",
            page_num, len(body), len(accumulated),
        )

        if len(body) < _DEFAULT_LIMIT:
            # Last page — fewer items than limit means no more data
            break

        offset += _DEFAULT_LIMIT
        page_num += 1

    logger.info(
        "fetch_all_issues: complete — %d issues across %d page(s)",
        len(accumulated), page_num,
    )
    return accumulated
