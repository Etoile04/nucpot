"""OKR metric utilities — pagination helpers and shared API client functions."""

from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_LIMIT = 1000


class PaperclipFetchError(RuntimeError):
    """Raised when the Paperclip API fetch fails.

    Distinct from arbitrary ``Exception`` so callers can distinguish a
    fetch failure from a programming error and degrade honestly to
    ``[no data]`` instead of rendering a numeric metric on top of a
    broken measurement.
    """


class PaperclipEmptyResultError(PaperclipFetchError):
    """The fetch succeeded but the result is unusable for measurement.

    Subclass of :class:`PaperclipFetchError` so every existing
    ``except PaperclipFetchError`` guard (report.py KR-1/KR-2/KR-4) keeps
    degrading to ``[no data]`` without needing a second handler, while
    callers that care can still distinguish "the call failed" from
    "the call returned an empty or unresolvable list".

    NFM-2443 closed the *raised-exception* path; this closes the
    *empty-but-successful* path (NFM-2492). A reader cannot distinguish
    ``commitEfficiency = 0.000`` (the team shipped nothing) from
    "0.000 because the lookup never matched", so we refuse to compute
    the metric in the latter case.
    """


def fetch_all_issues(
    api_url: str,
    company_id: str,
    params: dict[str, str],
    api_key: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch ALL issues from the Paperclip API, paginating automatically.

    Endpoint: ``GET /api/companies/{company_id}/issues``
    Pagination: ``offset`` + ``limit`` query parameters.
    Loop increments offset by limit until the response returns an empty list.

    Auth: sends ``Authorization: Bearer <api_key>``. A missing or empty
    ``api_key`` raises :class:`PaperclipFetchError` — we never proceed
    unauthenticated, since the upstream defect (NFM-2443) was exactly
    "call without auth → silent empty list → plausible-looking 0.000".

    Args:
        api_url: Paperclip API base URL (no trailing slash required).
        company_id: Company UUID for scoped issue endpoint.
        params: Additional query parameters merged into every request
            (e.g. ``{"status": "done"}``).
        api_key: Bearer token from ``$PAPERCLIP_API_KEY``. ``None`` or empty
            raises :class:`PaperclipFetchError`.

    Returns:
        A flat list of issue dicts accumulated from all pages.

    Raises:
        PaperclipFetchError: on missing/empty api_key, HTTP error, or
            malformed response. We never return a partial result list —
            partial data would silently corrupt downstream KR metrics.
    """
    if not api_key:
        raise PaperclipFetchError(
            "An API key is required to fetch issues from the Paperclip API "
            "(NFM-2443 AC1). Set $PAPERCLIP_API_KEY or pass api_key=... ."
        )

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

        request = urllib.request.Request(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as resp:
                body = json.loads(resp.read().decode())
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise PaperclipFetchError(
                f"fetch_all_issues: HTTP error on page {page_num} "
                f"(offset={offset}): {exc}"
            ) from exc
        except (json.JSONDecodeError, KeyError) as exc:
            raise PaperclipFetchError(
                f"fetch_all_issues: malformed response on page "
                f"{page_num}: {exc}"
            ) from exc

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