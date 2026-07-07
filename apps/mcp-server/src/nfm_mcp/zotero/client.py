"""Zotero API client wrapper.

Vendored from SMABoundless/zotero-mcp-server (MIT license).
Provides a thin async-friendly wrapper around pyzotero.Zotero.
All methods return new data structures (immutable pattern).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from pyzotero import zotero as zotero_lib

logger = logging.getLogger(__name__)


class ZoteroClient:
    """Async-safe wrapper around pyzotero.Zotero.

    Args:
        api_key: Zotero API key.
        user_id: Zotero user or group ID.
        library_type: ``"user"`` or ``"group"``.
    """

    def __init__(
        self,
        api_key: str,
        user_id: str,
        library_type: str = "user",
    ) -> None:
        self._zot = zotero_lib.Zotero(user_id, library_type, api_key)

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def search_items(
        self,
        query: str,
        limit: int = 25,
    ) -> list[dict[str, Any]]:
        """Search the Zotero library by keyword.

        Returns a list of raw Zotero item dicts.
        """
        return list(self._zot.items(q=query, limit=limit))

    def get_collections(self) -> list[dict[str, Any]]:
        """Return all collections with their keys and item counts."""
        return list(self._zot.collections())

    def get_collection_items(
        self,
        collection_key: str,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Return all items inside a specific collection."""
        return list(self._zot.collection_items(collection_key, limit=limit))

    def get_item(self, item_key: str) -> dict[str, Any]:
        """Return the full metadata record for a single item."""
        return self._zot.item(item_key)

    def get_recent_items(self, limit: int = 10) -> list[dict[str, Any]]:
        """Return the most recently added items."""
        return list(
            self._zot.items(limit=limit, sort="dateAdded", direction="desc")
        )

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def add_article(
        self,
        meta: dict[str, Any],
        collection_key: str | None = None,
    ) -> dict[str, Any]:
        """Add a single journal article to the library.

        Returns the Zotero API response dict.
        """
        template = _build_template(self._zot, meta)
        if collection_key:
            template["collections"] = [collection_key]
        return self._zot.create_items([template])

    def add_multiple_articles(
        self,
        articles: list[dict[str, Any]],
        collection_key: str | None = None,
    ) -> dict[str, Any]:
        """Batch-add articles.  API limit: 50 per call.

        Returns a dict with ``successful`` and ``failed`` keys.
        """
        templates = [_build_template(self._zot, a) for a in articles]
        if collection_key:
            for t in templates:
                t["collections"] = [collection_key]

        all_successful: dict[str, Any] = {}
        all_failed: dict[str, Any] = {}

        for i in range(0, len(templates), 50):
            resp = self._zot.create_items(templates[i : i + 50])
            if resp:
                all_successful.update(resp.get("successful", {}))
                all_failed.update(resp.get("failed", {}))

        return {"successful": all_successful, "failed": all_failed}

    def create_collection(
        self,
        name: str,
        parent_key: str | None = None,
    ) -> dict[str, Any]:
        """Create a new collection (folder).

        Returns the Zotero API response dict.
        """
        data: dict[str, Any] = {"name": name}
        if parent_key:
            data["parentCollection"] = parent_key
        return self._zot.create_collections([data])

    def add_item_to_collection(
        self,
        item_key: str,
        collection_key: str,
    ) -> None:
        """Add an existing item to a collection."""
        item = self._zot.item(item_key)
        data = dict(item["data"])
        cols = list(data.get("collections", []))
        if collection_key not in cols:
            cols.append(collection_key)
            data["collections"] = cols
            item["data"] = data
            self._zot.update_item(item)


# ------------------------------------------------------------------
# Formatting helpers
# ------------------------------------------------------------------


def format_item(item: dict[str, Any]) -> str:
    """Return a concise, readable summary of a Zotero item."""
    d = item.get("data", {})
    title = d.get("title", "No title")
    authors = ", ".join(
        f"{c.get('lastName', '')}, {c.get('firstName', '')}"
        for c in d.get("creators", [])
        if c.get("creatorType") == "author"
    )
    year = (d.get("date") or "")[:4]
    journal = d.get("publicationTitle", "")
    doi = d.get("DOI", "")
    key = item.get("key", "")

    lines = [f"[{key}] {title}"]
    if authors:
        lines.append(f"  Authors : {authors}")
    if journal:
        lines.append(f"  Journal : {journal}")
    if year:
        lines.append(f"  Year    : {year}")
    if doi:
        lines.append(f"  DOI     : {doi}")
    return "\n".join(lines)


def _build_template(
    zot: zotero_lib.Zotero,
    meta: dict[str, Any],
) -> dict[str, Any]:
    """Convert flat article metadata dict → Zotero journalArticle template."""
    t = zot.item_template("journalArticle")
    t["title"] = meta.get("title", "")

    authors = meta.get("authors", [])
    if authors:
        t["creators"] = [
            {
                "creatorType": "author",
                "firstName": a.get("firstName", ""),
                "lastName": a.get("lastName", ""),
            }
            for a in authors
            if isinstance(a, dict)
        ]

    if meta.get("journal"):
        t["publicationTitle"] = meta["journal"]
    if meta.get("year"):
        t["date"] = str(meta["year"])
    if meta.get("doi"):
        t["DOI"] = meta["doi"]
    if meta.get("abstract"):
        t["abstractNote"] = meta["abstract"]
    if meta.get("volume"):
        t["volume"] = str(meta["volume"])
    if meta.get("issue"):
        t["issue"] = str(meta["issue"])
    if meta.get("pages"):
        t["pages"] = meta["pages"]
    if meta.get("url"):
        t["url"] = meta["url"]

    extras: list[str] = []
    if meta.get("pmid"):
        extras.append(f"PMID: {meta['pmid']}")
    if meta.get("issn"):
        extras.append(f"ISSN: {meta['issn']}")
    if extras:
        t["extra"] = "\n".join(extras)

    return t
