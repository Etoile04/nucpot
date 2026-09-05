"""Crossref bibliographic metadata resolution (NFM-4313).

Resolves ``title`` / ``journal`` / ``year`` for a DOI from the Crossref
public API (``https://api.crossref.org/works/{doi}``), with the existing
fetched-markdown regex parse (:mod:`nfm_db.services.bibliographic_metadata`)
as a fallback.

Why Crossref (regression context)
---------------------------------
PR #1141's BUG-17 fix populated ``year`` / ``journal`` on the from-doi
path by regex-parsing the fetched Markdown.  The fetcher
(:mod:`nfm_db.services.doi_fetcher`) talks to Semantic Scholar, whose
abstract-only payload renders as ``# Title`` + abstract text — content
that can never match the journal regex (it requires a ``Journal …``
citation line ending in a volume number).  So even post-#1141, newly
ingested DOI rows kept ``journal = NULL`` and showed "—" in the
literature list.  Crossref's ``container-title`` is the authoritative
journal-of-record field and covers the stock rows the backfill script
has to heal.

Behavioural contract
--------------------
* Every public function is **best-effort**: a missing field yields
  ``None``, never a guess.  Callers only write non-``None`` values.
* :func:`fetch_crossref_metadata` never raises for network / status /
  payload problems — it returns ``None`` so ingestion is never gated on
  metadata availability (mirrors ``CrossrefBackend`` in
  ``scripts/doi_etl_admit.py``).
* The Crossref "polite pool" is opt-in via the ``CROSSREF_MAILTO`` env
  var (same convention as ``doi_etl_admit.py``); no key is required.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

import httpx

from nfm_db.services.bibliographic_metadata import extract_bibliographic_metadata

logger = logging.getLogger(__name__)

#: Inline XML markup (e.g. ``<mml:math>…</mml:math>``) inside Crossref
#: title / container-title strings.
_MARKUP_RE = re.compile(r"<[^>]+>")

#: Base URL of the Crossref works endpoint (DOI appended verbatim —
#: DOIs contain slashes and must stay in the path segment).
CROSSREF_API_BASE = "https://api.crossref.org/works/"

#: Timeout for the Crossref request (seconds).
REQUEST_TIMEOUT = 20.0

#: Plausible publication-year window; anything outside is treated as a
#: parse artefact (e.g. a DOI suffix leaking into ``date-parts``).
_MIN_YEAR = 1900
_MAX_YEAR = 2100

_FIELDS = ("title", "journal", "year")


# ---------------------------------------------------------------------------
# Pure message parsing
# ---------------------------------------------------------------------------


def _first_year_from_date_parts(field: Any) -> int | None:
    """Return the first plausible year inside a Crossref date field.

    Crossref date fields look like ``{"date-parts": [[2018, 5, 1]]}``;
    the first element of the first non-empty parts row is the year.
    Returns ``None`` for anything malformed or implausible.
    """
    if not isinstance(field, dict):
        return None
    parts_rows = field.get("date-parts")
    if not isinstance(parts_rows, list):
        return None
    for row in parts_rows:
        if not isinstance(row, list) or not row:
            continue
        candidate = row[0]
        if not isinstance(candidate, int):
            continue
        if _MIN_YEAR <= candidate <= _MAX_YEAR:
            return candidate
    return None


def _clean_str(value: Any) -> str | None:
    """Return *value* markup-free and stripped, or ``None`` when empty.

    Crossref ``title`` / ``container-title`` values may embed inline XML
    (MathML in titles is common for materials-science journals —
    observed live for ``10.1016/j.jnucmat.2018.05.039``).  Tags are
    stripped and whitespace collapsed so persisted rows stay readable.
    """
    if not isinstance(value, str):
        return None
    stripped = _MARKUP_RE.sub(" ", value)
    collapsed = " ".join(stripped.split())
    return collapsed or None


def parse_crossref_message(message: Any) -> dict[str, Any]:
    """Extract ``{title, journal, year}`` from a Crossref ``message``.

    Conservative by design: missing / malformed fields map to ``None``
    rather than raising, so callers can merge partial results.
    """
    if not isinstance(message, dict):
        return {"title": None, "journal": None, "year": None}

    title = None
    titles = message.get("title")
    if isinstance(titles, list) and titles:
        title = _clean_str(titles[0])

    journal = None
    container = message.get("container-title")
    if isinstance(container, list) and container:
        journal = _clean_str(container[0])

    year = None
    for date_field in (
        message.get("issued"),
        message.get("published-print"),
        message.get("published-online"),
        message.get("created"),
    ):
        year = _first_year_from_date_parts(date_field)
        if year is not None:
            break

    return {"title": title, "journal": journal, "year": year}


# ---------------------------------------------------------------------------
# HTTP wrapper
# ---------------------------------------------------------------------------


def fetch_crossref_metadata(
    doi: str,
    *,
    timeout: float = REQUEST_TIMEOUT,
    client: httpx.Client | None = None,
) -> dict[str, Any] | None:
    """Fetch ``{title, journal, year}`` for *doi* from Crossref.

    Returns ``None`` on any failure (HTTP error status, network error,
    malformed payload).  Never raises — metadata is best-effort and must
    not gate DOI ingestion.

    ``client`` allows tests to inject a mocked transport; production
    callers omit it.
    """
    mailto = os.environ.get("CROSSREF_MAILTO", "").strip()
    params = {"mailto": mailto} if mailto else None
    headers = {
        "User-Agent": "nucpot-nfmdb/1.0 (NFM-4313; +https://github.com/Etoile04/nucpot)",
        "Accept": "application/json",
    }
    url = f"{CROSSREF_API_BASE}{doi}"

    own_client = client is None
    http = client or httpx.Client()
    try:
        resp = http.get(url, params=params, headers=headers, timeout=timeout)
        if resp.status_code != 200:
            logger.info("crossref: doi=%s status=%d — no metadata", doi, resp.status_code)
            return None
        payload = resp.json()
        message = payload.get("message") if isinstance(payload, dict) else None
        if message is None:
            logger.warning("crossref: doi=%s payload missing 'message'", doi)
            return None
        return parse_crossref_message(message)
    except Exception:
        logger.warning("crossref: metadata fetch failed for doi=%s", doi, exc_info=True)
        return None
    finally:
        if own_client:
            http.close()


# ---------------------------------------------------------------------------
# Merge semantics — Crossref authoritative, markdown parse fallback
# ---------------------------------------------------------------------------


def resolve_doi_bibliography(doi: str, md_content: str | None = None) -> dict[str, Any]:
    """Resolve ``{title, journal, year}`` for *doi*.

    Combines the two available sources:

    1. **Markdown regex parse** of *md_content* (broad fallback — works
       when the fetched content embeds a citation line).
    2. **Crossref** (authoritative — journal-of-record ``container-title``
       and issued date win over any regex guess).

    Fields where Crossref has no value keep the markdown-parse value,
    so a partial Crossref record does not erase a good regex hit.
    """
    resolved: dict[str, Any] = {"title": None, "journal": None, "year": None}

    if md_content:
        try:
            biblio = extract_bibliographic_metadata(md_content)
        except Exception:  # pragma: no cover — parse is total, defensive only
            logger.warning("crossref: markdown biblio parse failed for doi=%s", doi)
            biblio = {}
        for field in _FIELDS:
            value = biblio.get(field)
            if value is not None:
                resolved[field] = value

    crossref = fetch_crossref_metadata(doi)
    if crossref is not None:
        for field in _FIELDS:
            value = crossref.get(field)
            if value is not None:
                resolved[field] = value

    return resolved


__all__ = [
    "CROSSREF_API_BASE",
    "REQUEST_TIMEOUT",
    "fetch_crossref_metadata",
    "parse_crossref_message",
    "resolve_doi_bibliography",
]
