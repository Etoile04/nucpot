"""Safe Paperclip issue lookups — the only sanctioned way to fetch or list issues.

ADR-008 / NFM-2036. See the post-mortem on NFM-1909.

Why this module exists
----------------------
Three Paperclip API behaviours make a *live* issue look deleted. On 2026-07-29
an agent hit two of them and asserted across three heartbeats that NFM-1909 had
been deleted. It had not.

    | Call                                         | Actual response                       | Misread as     |
    |----------------------------------------------|---------------------------------------|----------------|
    | GET /api/issues/{id}         (no auth)       | 401 {"error": "Unauthorized"}         | 404 / deleted  |
    | GET /api/issues?limit=5      (no companyId)  | 400 {"error": "Missing companyId..."} | len() == 0     |
    | GET /api/companies/{id}/issues?limit=2000    | 1000 rows, silently truncated         | complete set   |
    | GET /api/companies/{id}/issues/{uuid}        | row present but expanded fields missing | no blockers    |

The fourth row is the **trap-3** pitfall: the collection endpoint silently
strips ``blockedBy``, ``comments``, ``ancestors``, ``blocks``,
``terminalBlockers``, ``planDocument``, and ``workProducts``.  An agent
that reads blockers through the collection path concludes "no blockers"
even when the issue is genuinely blocked.

``lookup_issue()`` mitigates trap-3 by resolving the UUID through the
collection, then re-fetching via the bare ``GET /api/issues/{uuid}``
endpoint which returns the full expanded payload.  ``lookup_issues()``
deliberately stays on the collection — callers must NOT trust
``blockedBy`` from list reads.

Every one of those reaches a naive caller as "zero issues". This module makes
that shape unreachable.

The safety property
-------------------
**An empty result can only ever mean "the API really has no matching issues".**
Auth and wrong-path failures are *raised*, never returned, so they can never be
mistaken for an empty list. A genuine no-match is ``Ok(issues=[])``.

Raised vs returned
------------------
``LookupResult`` is the complete result vocabulary, but the two members that
describe a broken *caller environment* arrive as exceptions rather than return
values:

* **Raised** — ``AuthError``, ``WrongPath``. Caller/config bugs (missing key,
  hand-built URL). Failing loud is the point: a returned error object can be
  ignored, an exception cannot.
* **Returned** — ``Ok``, ``NotFound``, ``ApiError``. Genuine remote outcomes the
  caller is expected to branch on.

Both raised types are frozen dataclasses *and* exceptions, so they carry
structured fields either way.

Usage
-----
    from paperclip_issue_lookup import lookup_issue, Ok, NotFound

    result = lookup_issue("NFM-1909")
    match result:
        case Ok(issues=[issue]):
            print(issue["title"])
        case NotFound(identifier=ident):
            print(f"{ident} genuinely does not exist")

Never conclude "the issue was deleted" from anything other than ``NotFound``.
"""

# ruff: noqa: N818, UP007
from __future__ import annotations

import os
import re
import urllib.parse
from dataclasses import dataclass
from typing import Any, Union

import requests

__all__ = [
    "ApiError",
    "AuthError",
    "LookupResult",
    "NotFound",
    "Ok",
    "WrongPath",
    "WrongPathError",
    "lookup_issue",
    "lookup_issues",
]

# --- Configuration -----------------------------------------------------------
# BASE_URL is the *full issues-collection endpoint*, not just the host. The path
# validator inspects it on every call, so patching it to a bare `/api/issues`
# path is caught before any socket is opened.

API_ROOT = os.environ.get("PAPERCLIP_API_URL", "").rstrip("/")
COMPANY_ID = os.environ.get("PAPERCLIP_COMPANY_ID", "")

BASE_URL = f"{API_ROOT}/api/companies/{COMPANY_ID}/issues"

PAGE_SIZE = 1000
DEFAULT_MAX_PAGES = 10
DEFAULT_TIMEOUT = 30.0

# A valid issues endpoint must be company-scoped. This is the trap-2 guard.
_COMPANY_SCOPED = re.compile(r"/api/companies/[^/]+/issues/?$")

_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


# --- Result vocabulary -------------------------------------------------------


@dataclass(frozen=True)
class Ok:
    """A successful lookup. ``issues`` may legitimately be empty."""

    issues: list[dict]
    pages_consumed: int
    truncated: bool = False


@dataclass(frozen=True)
class NotFound:
    """The identifier was searched for and genuinely has no match."""

    identifier: str
    http_status: int = 404


@dataclass(frozen=True)
class ApiError:
    """The API returned something we could not interpret as issues."""

    http_status: int
    body: str
    kind: str  # "server" | "rate_limited" | "unknown"


@dataclass(frozen=True)
class AuthError(Exception):
    """Raised when credentials are missing or rejected. Never a "not found"."""

    http_status: int
    message: str
    hint: str
    preflight: bool = False

    def __str__(self) -> str:
        stage = "pre-flight" if self.preflight else f"HTTP {self.http_status}"
        return f"[{stage}] {self.message} — {self.hint}"


@dataclass(frozen=True)
class WrongPath(Exception):
    """Raised when the endpoint is not company-scoped. Never a "not found"."""

    called_path: str
    hint: str

    def __str__(self) -> str:
        return f"refusing to call {self.called_path!r} — {self.hint}"


# The acceptance spec names this type `WrongPathError`; both names are supported.
WrongPathError = WrongPath

LookupResult = Union[Ok, AuthError, NotFound, WrongPath, ApiError]


# --- Pre-flight guards -------------------------------------------------------


def _api_key() -> str:
    """Read the key at call time so tests can unset it without reimporting."""
    key = os.environ.get("PAPERCLIP_API_KEY", "").strip()
    if not key:
        raise AuthError(
            http_status=401,
            message="PAPERCLIP_API_KEY is missing or empty",
            hint="missing or invalid PAPERCLIP_API_KEY; no HTTP request was attempted",
            preflight=True,
        )
    return key


def _validated_url() -> str:
    """Reject any endpoint that is not company-scoped, before opening a socket."""
    url = BASE_URL
    path = urllib.parse.urlparse(url).path
    if not _COMPANY_SCOPED.search(path):
        raise WrongPath(
            called_path=url,
            hint=(
                "use /api/companies/{companyId}/issues — the bare /api/issues path "
                "returns an error object that a naive len() reads as 'zero issues match'"
            ),
        )
    return url


# --- HTTP --------------------------------------------------------------------


def _get(url: str, params: dict[str, Any], key: str) -> requests.Response:
    """Single funnel for every outbound request, so stubbing `requests` catches all."""
    return requests.get(
        url,
        params=params,
        headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
        timeout=DEFAULT_TIMEOUT,
    )


def _error_kind(status: int) -> str:
    if status == 429:
        return "rate_limited"
    if status >= 500:
        return "server"
    return "unknown"


def _rows_from(response: requests.Response) -> list[dict] | ApiError:
    """Extract issue rows, refusing to treat an error object as an empty list."""
    if response.status_code in (401, 403):
        raise AuthError(
            http_status=response.status_code,
            message=f"API rejected the credentials ({response.status_code})",
            hint="missing or invalid PAPERCLIP_API_KEY",
        )

    try:
        body = response.json()
    except ValueError:
        return ApiError(
            http_status=response.status_code,
            body=response.text[:500],
            kind="unknown",
        )

    # An `{"error": ...}` object is never zero results — it is a failure.
    if isinstance(body, dict) and "error" in body:
        return ApiError(
            http_status=response.status_code,
            body=str(body["error"])[:500],
            kind=_error_kind(response.status_code),
        )

    if isinstance(body, list):
        return body
    if isinstance(body, dict) and isinstance(body.get("issues"), list):
        return body["issues"]

    return ApiError(
        http_status=response.status_code,
        body=f"unexpected response shape: {type(body).__name__}",
        kind="unknown",
    )


def _paginate(params: dict[str, Any], max_pages: int) -> tuple[list[dict], int, bool] | ApiError:
    """Walk `offset` in PAGE_SIZE chunks until a short read or the page cap."""
    key = _api_key()
    url = _validated_url()

    rows: list[dict] = []
    pages = 0
    truncated = False

    while pages < max_pages:
        page_params = {**params, "limit": PAGE_SIZE, "offset": pages * PAGE_SIZE}
        chunk = _rows_from(_get(url, page_params, key))
        if isinstance(chunk, ApiError):
            return chunk

        pages += 1
        rows.extend(chunk)

        if len(chunk) < PAGE_SIZE:
            break
    else:
        # Cap reached without a short read: more rows may exist upstream.
        truncated = True

    return rows, max(pages, 1), truncated


# --- Expanded single-issue fetch (trap-3 mitigation) ------------------------

# The collection endpoint (GET /api/companies/{id}/issues) silently strips
# several expanded fields: blockedBy, comments, ancestors, blocks,
# terminalBlockers, planDocument, workProducts.  Reading blockers through
# the collection path always reports "no blockers" even when they exist.
#
# The bare per-issue endpoint (GET /api/issues/{uuid}) returns the full
# expanded payload including all of the above.  This module therefore
# resolves the UUID through the collection, then re-fetches via the bare
# endpoint so callers of ``lookup_issue()`` see the true ``blockedBy`` set.
#
# ``lookup_issues()`` deliberately stays on the collection endpoint —
# callers must NOT trust ``blockedBy`` from list reads.


def _issue_from(response: requests.Response) -> dict | ApiError:
    """Extract a single expanded issue dict from a bare ``GET /api/issues/{uuid}``."""
    if response.status_code in (401, 403):
        raise AuthError(
            http_status=response.status_code,
            message=f"API rejected the credentials ({response.status_code})",
            hint="missing or invalid PAPERCLIP_API_KEY",
        )

    try:
        body = response.json()
    except ValueError:
        return ApiError(
            http_status=response.status_code,
            body=response.text[:500],
            kind="unknown",
        )

    if isinstance(body, dict) and "error" in body:
        return ApiError(
            http_status=response.status_code,
            body=str(body["error"])[:500],
            kind=_error_kind(response.status_code),
        )

    if isinstance(body, dict) and "id" in body:
        return body

    return ApiError(
        http_status=response.status_code,
        body=f"unexpected response shape: {type(body).__name__}",
        kind="unknown",
    )


def _fetch_expanded_issue(uuid: str) -> dict | ApiError:
    """Fetch a single issue by UUID via the bare ``GET /api/issues/{uuid}``.

    This intentionally bypasses :func:`_validated_url` because the bare
    per-issue endpoint is *not* company-scoped — that scoping guard only
    applies to collection operations.
    """
    key = _api_key()
    url = f"{API_ROOT}/api/issues/{uuid}"
    response = _get(url, {}, key)
    return _issue_from(response)


def _clean(params: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in params.items() if v not in (None, "", [])}


def _same(a: Any, b: str) -> bool:
    return isinstance(a, str) and a.strip().casefold() == b.casefold()


# --- Public surface ----------------------------------------------------------


def lookup_issue(identifier: str, max_pages: int = DEFAULT_MAX_PAGES) -> LookupResult:
    """Fetch exactly one issue by identifier (``"NFM-1909"``) or UUID.

    Returns ``Ok`` with a single-element list, or ``NotFound``.
    Raises ``AuthError`` / ``WrongPath`` — see the module docstring.

    The returned issue dict includes expanded fields (``blockedBy``,
    ``comments``, ``ancestors``, etc.) by re-fetching via the bare per-issue
    endpoint after UUID resolution.  This is the trap-3 mitigation — see
    module docstring.
    """
    ident = (identifier or "").strip()
    if not ident:
        raise ValueError("identifier must be a non-empty string")

    # Fast path: caller already has the UUID — skip the collection entirely.
    if _UUID.match(ident):
        expanded = _fetch_expanded_issue(ident)
        if isinstance(expanded, ApiError):
            return expanded
        return Ok(issues=[expanded], pages_consumed=1)

    # The server's `q` search is relevance-ranked and fuzzy: it returns unrelated
    # rows for a nonsense identifier, so we must exact-match locally.
    query = _clean({"q": ident})

    page = _paginate(query, max_pages)
    if isinstance(page, ApiError):
        return page
    rows, pages, truncated = page

    for row in rows:
        if _same(row.get("identifier"), ident):
            # Re-fetch via the bare per-issue endpoint to get expanded fields
            # (blockedBy, comments, ancestors, etc.) that the collection strips.
            row_uuid = row.get("id")
            if row_uuid:
                expanded = _fetch_expanded_issue(row_uuid)
                if isinstance(expanded, ApiError):
                    # Fall back to the collection row if expanded fetch fails.
                    return Ok(issues=[row], pages_consumed=pages, truncated=truncated)
                return Ok(issues=[expanded], pages_consumed=pages, truncated=truncated)
            return Ok(issues=[row], pages_consumed=pages, truncated=truncated)

    if truncated:
        # We never saw the whole collection, so "absent" is not a safe claim.
        return ApiError(
            http_status=200,
            body=(
                f"scanned {pages} pages ({len(rows)} rows) without finding {ident!r}, "
                "but the page cap was hit — raise max_pages before concluding anything"
            ),
            kind="unknown",
        )

    return NotFound(identifier=ident)


def lookup_issues(
    q: str | None = None,
    status: list[str] | None = None,
    assignee_agent_id: str | None = None,
    project_id: str | None = None,
    max_pages: int = DEFAULT_MAX_PAGES,
) -> LookupResult:
    """List issues with optional filters, auto-paginating past the 1000-row cap.

    Returns ``Ok`` (possibly with an empty ``issues`` list — that means the API
    genuinely matched nothing). Inspect ``Ok.truncated`` before treating the
    result as the complete set. Raises ``AuthError`` / ``WrongPath``.
    """
    params = _clean(
        {
            "q": q,
            "status": ",".join(status) if status else None,
            "assigneeAgentId": assignee_agent_id,
            "projectId": project_id,
        }
    )

    page = _paginate(params, max_pages)
    if isinstance(page, ApiError):
        return page

    rows, pages, truncated = page
    return Ok(issues=rows, pages_consumed=pages, truncated=truncated)
