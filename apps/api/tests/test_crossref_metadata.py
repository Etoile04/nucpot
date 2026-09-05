"""Unit tests for ``nfm_db.services.crossref_metadata`` (NFM-4313).

Covers the Crossref bibliographic resolution used by both the from-doi
incremental path and the stock backfill script:

* :func:`parse_crossref_message` — pure message → {title, journal, year}
  extraction from a Crossref ``/works/{doi}`` payload.
* :func:`fetch_crossref_metadata` — best-effort HTTP wrapper (never
  raises; returns ``None`` on 404 / network error / malformed payload).
* :func:`resolve_doi_bibliography` — merge semantics: Crossref is
  authoritative, the fetched-markdown regex parse is the fallback.
"""

from __future__ import annotations

import httpx
import pytest
from httpx import MockTransport, Response

from nfm_db.services.crossref_metadata import (
    fetch_crossref_metadata,
    parse_crossref_message,
    resolve_doi_bibliography,
)

# ---------------------------------------------------------------------------
# Realistic Crossref /works payload (trimmed to the fields we consume)
# ---------------------------------------------------------------------------

FULL_MESSAGE: dict = {
    "title": ["Thermal conductivity of UO2 fuel"],
    "container-title": ["Journal of Nuclear Materials"],
    "issued": {"date-parts": [[2018, 5, 1]]},
}

# ---------------------------------------------------------------------------
# parse_crossref_message
# ---------------------------------------------------------------------------


class TestParseCrossrefMessage:
    def test_extracts_title_journal_year(self) -> None:
        parsed = parse_crossref_message(FULL_MESSAGE)
        assert parsed["title"] == "Thermal conductivity of UO2 fuel"
        assert parsed["journal"] == "Journal of Nuclear Materials"
        assert parsed["year"] == 2018

    def test_journal_takes_first_container_title(self) -> None:
        msg = {
            "container-title": ["Journal of Nuclear Materials", "Redundant Title"],
        }
        parsed = parse_crossref_message(msg)
        assert parsed["journal"] == "Journal of Nuclear Materials"

    def test_empty_container_title_list_is_none(self) -> None:
        parsed = parse_crossref_message({"container-title": []})
        assert parsed["journal"] is None

    def test_non_list_container_title_is_none(self) -> None:
        # Defensive: Crossref always sends a list, but a proxy / API change
        # could send a bare string — prefer None over a crash.
        parsed = parse_crossref_message({"container-title": "Journal of Nuclear Materials"})
        assert parsed["journal"] is None

    def test_year_takes_first_issued_date_part(self) -> None:
        parsed = parse_crossref_message({"issued": {"date-parts": [[2020, 3]]}})
        assert parsed["year"] == 2020

    def test_year_falls_back_to_published_online(self) -> None:
        # Common for early-view articles: ``issued`` may carry only the
        # print year while ``published-online`` has the real online year —
        # both are legitimate publication years; either is acceptable, and
        # the fallback order is issued → published-print → published-online
        # → created.
        msg = {
            "published-print": {"date-parts": [[2019]]},
            "published-online": {"date-parts": [[2018]]},
            "created": {"date-parts": [[2017]]},
        }
        parsed = parse_crossref_message(msg)
        assert parsed["year"] == 2019

    def test_year_falls_back_to_created_when_no_published(self) -> None:
        msg = {"created": {"date-parts": [[2016, 9, 2]]}}
        parsed = parse_crossref_message(msg)
        assert parsed["year"] == 2016

    def test_year_none_when_all_date_fields_missing(self) -> None:
        parsed = parse_crossref_message({"title": ["T"]})
        assert parsed["year"] is None

    def test_year_ignores_malformed_date_parts(self) -> None:
        msg = {"issued": {"date-parts": [["not-a-year"]]}}
        parsed = parse_crossref_message(msg)
        assert parsed["year"] is None

    def test_year_rejects_implausible_years(self) -> None:
        # DOI suffixes sometimes leak into date fields; a "year" of 10023
        # or 42 is garbage, not a publication year.
        parsed = parse_crossref_message({"issued": {"date-parts": [[10023]]}})
        assert parsed["year"] is None

    def test_title_none_when_missing_or_empty(self) -> None:
        assert parse_crossref_message({})["title"] is None
        assert parse_crossref_message({"title": []})["title"] is None
        assert parse_crossref_message({"title": ["  "]})["title"] is None

    def test_title_is_stripped(self) -> None:
        parsed = parse_crossref_message({"title": ["  A Title  "]})
        assert parsed["title"] == "A Title"

    def test_empty_message_returns_all_none(self) -> None:
        parsed = parse_crossref_message({})
        assert parsed == {"title": None, "journal": None, "year": None}

    def test_title_strips_inline_mathml_markup(self) -> None:
        # Observed live for 10.1016/j.jnucmat.2018.05.039 — Crossref
        # embeds MathML in titles; persisted rows must stay readable.
        msg = {
            "title": [
                "Calculation of the displacement energy of\n"
                "  <mml:math><mml:mi>α</mml:mi></mml:math> and\n"
                "  <mml:math><mml:mi>γ</mml:mi></mml:math> uranium"
            ],
            "container-title": ["Journal of Nuclear Materials"],
            "issued": {"date-parts": [[2018]]},
        }
        parsed = parse_crossref_message(msg)
        assert parsed["title"] == (
            "Calculation of the displacement energy of α and γ uranium"
        )


# ---------------------------------------------------------------------------
# fetch_crossref_metadata (HTTP wrapper, mocked transport)
# ---------------------------------------------------------------------------


def _client_with(handler) -> httpx.Client:
    return httpx.Client(transport=MockTransport(handler))


class TestFetchCrossrefMetadata:
    def test_200_returns_parsed_metadata(self) -> None:
        payload = {"status": "ok", "message": FULL_MESSAGE}

        def handler(request: httpx.Request) -> Response:
            return Response(200, json=payload)

        client = _client_with(handler)
        result = fetch_crossref_metadata("10.1016/j.jnucmat.2018.05.039", client=client)
        assert result is not None
        assert result["journal"] == "Journal of Nuclear Materials"
        assert result["year"] == 2018
        assert result["title"] == "Thermal conductivity of UO2 fuel"

    def test_404_returns_none(self) -> None:
        def handler(request: httpx.Request) -> Response:
            return Response(404, json={"status": "error"})

        client = _client_with(handler)
        assert fetch_crossref_metadata("10.9999/does-not-exist", client=client) is None

    def test_rate_limited_returns_none(self) -> None:
        def handler(request: httpx.Request) -> Response:
            return Response(429, text="slow down")

        client = _client_with(handler)
        assert fetch_crossref_metadata("10.1/x", client=client) is None

    def test_malformed_json_returns_none(self) -> None:
        def handler(request: httpx.Request) -> Response:
            return Response(200, text="<html>not json</html>")

        client = _client_with(handler)
        assert fetch_crossref_metadata("10.1/x", client=client) is None

    def test_network_error_returns_none(self) -> None:
        def handler(request: httpx.Request) -> Response:
            raise httpx.ConnectError("boom")

        client = _client_with(handler)
        assert fetch_crossref_metadata("10.1/x", client=client) is None

    def test_missing_message_key_returns_none(self) -> None:
        def handler(request: httpx.Request) -> Response:
            return Response(200, json={"status": "ok"})

        client = _client_with(handler)
        assert fetch_crossref_metadata("10.1/x", client=client) is None

    def test_uses_polite_pool_mailto_when_configured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen_urls: list[str] = []

        def handler(request: httpx.Request) -> Response:
            seen_urls.append(str(request.url))
            return Response(200, json={"message": FULL_MESSAGE})

        monkeypatch.setenv("CROSSREF_MAILTO", "ops@example.org")
        client = _client_with(handler)
        fetch_crossref_metadata("10.1016/j.jnucmat.2018.05.039", client=client)
        # The query value is percent-encoded in the raw URL, so compare
        # the decoded param instead of substring-matching the URL.
        mailtos = [httpx.URL(url).params.get("mailto") for url in seen_urls]
        assert "ops@example.org" in mailtos

    def test_doi_slashes_survive_in_path(self) -> None:
        seen_urls: list[str] = []

        def handler(request: httpx.Request) -> Response:
            seen_urls.append(str(request.url))
            return Response(200, json={"message": FULL_MESSAGE})

        client = _client_with(handler)
        # DOIs contain slashes, which must survive in the path segment.
        fetch_crossref_metadata("10.1016/j.jnucmat.2018.05.039", client=client)
        assert seen_urls and seen_urls[0].endswith("10.1016/j.jnucmat.2018.05.039")


# ---------------------------------------------------------------------------
# resolve_doi_bibliography — merge semantics
# ---------------------------------------------------------------------------


class TestResolveDoiBibliography:
    def test_crossref_overrides_markdown_parse(self, monkeypatch: pytest.MonkeyPatch) -> None:
        md = "# Markdown Title\n\nJournal of Nuclear Materials 512 (1999)\n"
        monkeypatch.setattr(
            "nfm_db.services.crossref_metadata.fetch_crossref_metadata",
            lambda doi, **kw: {
                "title": "Crossref Title",
                "journal": "Journal of Nuclear Materials",
                "year": 2018,
            },
        )
        resolved = resolve_doi_bibliography("10.1/x", md)
        assert resolved["title"] == "Crossref Title"
        assert resolved["journal"] == "Journal of Nuclear Materials"
        assert resolved["year"] == 2018

    def test_crossref_unavailable_falls_back_to_markdown(self, monkeypatch: pytest.MonkeyPatch) -> None:
        md = "# Markdown Title\n\nJournal of Nuclear Materials 512 (1999)\n"
        monkeypatch.setattr(
            "nfm_db.services.crossref_metadata.fetch_crossref_metadata",
            lambda doi, **kw: None,
        )
        resolved = resolve_doi_bibliography("10.1/x", md)
        assert resolved["title"] == "Markdown Title"
        assert resolved["journal"] == "Journal of Nuclear Materials"
        assert resolved["year"] == 1999

    def test_crossref_gaps_filled_from_markdown(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Crossref may return a journal but no year (rare but real for
        # ahead-of-print records); the markdown year should survive.
        md = "# Markdown Title\n\nJournal of Nuclear Materials 512 (1999)\n"
        monkeypatch.setattr(
            "nfm_db.services.crossref_metadata.fetch_crossref_metadata",
            lambda doi, **kw: {"title": None, "journal": "Journal of Nuclear Materials", "year": None},
        )
        resolved = resolve_doi_bibliography("10.1/x", md)
        assert resolved["journal"] == "Journal of Nuclear Materials"
        assert resolved["year"] == 1999

    def test_both_unavailable_returns_nones(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "nfm_db.services.crossref_metadata.fetch_crossref_metadata",
            lambda doi, **kw: None,
        )
        resolved = resolve_doi_bibliography("10.1/x", "# Only a title\n\nPlain abstract text.")
        assert resolved == {"title": "Only a title", "journal": None, "year": None}

    def test_abstract_only_markdown_yields_no_journal(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # The regression that motivated NFM-4313: Semantic Scholar
        # abstract-only markdown has no journal citation line, so the
        # markdown regex returns None and — without Crossref — the row
        # used to land in the literature list with "—" columns.
        md = "# Some Title\n\nThis paper investigates UO2 under irradiation.\n"
        monkeypatch.setattr(
            "nfm_db.services.crossref_metadata.fetch_crossref_metadata",
            lambda doi, **kw: {
                "title": "Some Title",
                "journal": "Journal of Nuclear Materials",
                "year": 2018,
            },
        )
        resolved = resolve_doi_bibliography("10.1/x", md)
        assert resolved["journal"] == "Journal of Nuclear Materials"
        assert resolved["year"] == 2018
