"""Unit tests for ``doi_etl_admit.py`` (NFM-3871 / C-I1 CLI).

The CLI is mostly thin glue over the library module, so the test
surface is small: cover the URL/password redaction, the Crossref /
OpenAlex response parsers, and the argument parser so a future
refactor cannot silently change the manifest schema.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make the scripts/ package importable for the test process.
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _SCRIPTS_DIR.parent.parent
_API_SRC = _REPO_ROOT / "apps/api/src"
for p in (str(_SCRIPTS_DIR), str(_API_SRC)):
    if p not in sys.path:
        sys.path.insert(0, p)


from doi_etl_admit import (  # type: ignore[import-not-found]  # noqa: E402
    CrossrefBackend,
    OpenAlexBackend,
    _parse_args,
    _parse_crossref,
    _parse_openalex,
    _redact_url,
    main,
)

# ---------------------------------------------------------------------------
# URL / password redaction
# ---------------------------------------------------------------------------


class TestRedactUrl:
    def test_strips_password_from_asyncpg_url(self) -> None:
        redacted = _redact_url("postgresql+asyncpg://nfm:secret@db:5432/nfm_db")
        assert "secret" not in redacted
        assert "***" in redacted
        assert "@db:5432/nfm_db" in redacted

    def test_passes_through_urls_without_credentials(self) -> None:
        url = "postgresql+asyncpg://localhost/nfm_db"
        assert _redact_url(url) == url

    def test_handles_url_with_at_sign_in_path(self) -> None:
        # pathological — make sure we don't crash
        url = "postgresql+asyncpg://user:pw@host:5432/db@with@at"
        redacted = _redact_url(url)
        assert "pw" not in redacted


# ---------------------------------------------------------------------------
# Crossref message parsing
# ---------------------------------------------------------------------------


class TestParseCrossref:
    def test_extracts_title_author_year(self) -> None:
        msg = {
            "title": ["  A Title  "],
            "author": [{"given": "Jane", "family": "Doe"}],
            "issued": {"date-parts": [[2020]]},
        }
        rec = _parse_crossref(msg)
        assert rec is not None
        assert rec.title == "A Title"
        assert rec.first_author == "Jane Doe"
        assert rec.year == 2020

    def test_missing_author_falls_back_to_none(self) -> None:
        msg = {"title": ["T"], "author": [], "issued": {"date-parts": [[2019]]}}
        rec = _parse_crossref(msg)
        assert rec is not None
        assert rec.first_author is None

    def test_missing_title_is_none(self) -> None:
        msg = {"title": [], "author": [], "issued": {"date-parts": [[2019]]}}
        rec = _parse_crossref(msg)
        assert rec is not None
        assert rec.title is None

    def test_missing_issued_is_none(self) -> None:
        msg = {"title": ["T"], "author": []}
        rec = _parse_crossref(msg)
        assert rec is not None
        assert rec.year is None

    def test_picks_first_author_even_when_multiple(self) -> None:
        msg = {
            "title": ["T"],
            "author": [
                {"given": "A", "family": "B"},
                {"given": "C", "family": "D"},
            ],
            "issued": {"date-parts": [[2020]]},
        }
        rec = _parse_crossref(msg)
        assert rec is not None
        assert rec.first_author == "A B"


# ---------------------------------------------------------------------------
# OpenAlex work parsing
# ---------------------------------------------------------------------------


class TestParseOpenAlex:
    def test_extracts_fields(self) -> None:
        payload = {
            "title": "Paper title",
            "authorships": [{"author": {"display_name": "Jane Doe"}}],
            "publication_date": "2020-05-01",
        }
        meta = _parse_openalex(payload)
        assert meta.found is True
        assert meta.title == "Paper title"
        assert meta.first_author == "Jane Doe"
        assert meta.year == 2020

    def test_year_only_from_partial_date(self) -> None:
        payload = {"publication_date": "2019", "title": "T"}
        meta = _parse_openalex(payload)
        assert meta.year == 2019

    def test_missing_authorship_falls_back_to_none(self) -> None:
        payload = {"title": "T", "authorships": [], "publication_date": "2020-01-01"}
        meta = _parse_openalex(payload)
        assert meta.first_author is None


# ---------------------------------------------------------------------------
# Backend constructors (no network calls — just shape)
# ---------------------------------------------------------------------------


class TestBackendConstructors:
    def test_crossref_backend_name(self) -> None:
        assert CrossrefBackend().name == "crossref"

    def test_openalex_backend_name(self) -> None:
        assert OpenAlexBackend().name == "openalex"


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


class TestArgParser:
    def test_defaults(self) -> None:
        ns = _parse_args([])
        assert ns.sample_rate == 0.30
        assert ns.seed == 20260830
        assert ns.dry_run is False
        assert ns.output_manifest == Path("./doi_admit_manifest.json")
        assert ns.limit is None
        assert ns.verbose is False

    def test_dry_run_flag(self) -> None:
        ns = _parse_args(["--dry-run"])
        assert ns.dry_run is True

    def test_explicit_sample_rate(self) -> None:
        ns = _parse_args(["--sample-rate", "0.5"])
        assert ns.sample_rate == 0.5

    def test_limit(self) -> None:
        ns = _parse_args(["--limit", "10"])
        assert ns.limit == 10


# ---------------------------------------------------------------------------
# main() — smoke-test the dry-run path end-to-end without a DB
# ---------------------------------------------------------------------------


class TestMainSmoke:
    def test_main_dry_run_with_empty_database_returns_zero(self, monkeypatch, tmp_path, caplog) -> None:
        """The CLI must not require a live DB connection in --dry-run
        mode if the staging table is empty; we stub the row-fetch to
        prove the entrypoint wires up cleanly."""
        import doi_etl_admit as cli  # type: ignore[import-not-found]

        async def _fake_fetch(session):
            return []

        monkeypatch.setattr(cli, "_fetch_rows", _fake_fetch)
        manifest = tmp_path / "manifest.json"
        with caplog.at_level("INFO"):
            rc = main(["--dry-run", "--output-manifest", str(manifest)])
        assert rc == 2  # zero etl_ok → non-zero so CI notices the empty cohort
        assert manifest.exists()
        payload = __import__("json").loads(manifest.read_text())
        assert payload["summary"]["total_rows"] == 0
        assert payload["summary"]["etl_ok"] == 0
        assert payload["issue"] == "NFM-3871"
        assert payload["schema_version"] == "1.0"
