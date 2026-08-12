"""Tests for ``_load_v2_content`` (NFM-2909).

Strangler-fig content-loading contract for the V2 pipeline::

    file         → load from path on disk
    doi          → try as file path; fall back to stub content in
                   ``EXTRACTION_STUB_MODE`` so the 5-step orchestrator
                   can run end-to-end during CI / dev. Out-of-stub DOI
                   resolution is intentionally out of scope for the
                   strangler-fig flip and is documented in
                   ``docs/architecture/ADR-NFM-2737-...md``.
    url,
    datasource   → explicit ``NotImplementedError`` with the
                   documented migration path. Staging / prod traffic
                   does not yet exercise these source types.

Tested in isolation so the loader contract is locked before the
``EXTRACTION_PIPELINE_V2`` flag flip to default-True (NFM-2869).
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_stub_mode(monkeypatch):
    """Default each case to a known stub-mode state.

    ``_load_v2_content`` consults ``EXTRACTION_STUB_MODE``; tests that
    want a specific value set it explicitly with ``monkeypatch.setenv``.
    """
    monkeypatch.delenv("EXTRACTION_STUB_MODE", raising=False)


def _loader():
    from nfm_db.services.extraction_pipeline_dispatch import (
        _load_v2_content,
    )

    return _load_v2_content


def test_file_source_type_returns_file_contents(tmp_path):
    """``source_type='file'`` reads from the path on disk."""
    sample = tmp_path / "paper.md"
    sample.write_text("# Material\n\nUO2 properties.", encoding="utf-8")

    result = _loader()(str(sample), "file")

    assert result == "# Material\n\nUO2 properties."


def test_file_source_type_raises_when_missing(tmp_path):
    """Missing file path is still a hard ``FileNotFoundError`` even in
    stub mode — the caller asked for a real file and the loader must
    not silently fabricate one."""
    missing = tmp_path / "does_not_exist.md"

    with pytest.raises(FileNotFoundError, match="does_not_exist.md"):
        _loader()(str(missing), "file")


def test_doi_source_type_with_local_pdf(tmp_path):
    """``source_type='doi'`` accepts a file path that happens to hold a
    PDF / markdown copy of the paper (NFM-2909 acceptance: same
    resolution path V1 uses for locally-resolved PDFs)."""
    sample = tmp_path / "doi_paper.md"
    sample.write_text("# DOI paper\n\nProperties here.", encoding="utf-8")

    result = _loader()(str(sample), "doi")

    assert result == "# DOI paper\n\nProperties here."


def test_doi_source_type_stub_mode_returns_placeholder(monkeypatch):
    """When the DOI reference is not on disk and ``EXTRACTION_STUB_MODE``
    is active, the loader returns a stable placeholder string so the
    5-step orchestrator can still run end-to-end.

    This is what unblocks the 42 tests on PR #790 that previously
    failed with ``FileNotFoundError: test_paper.md`` — they exercise
    V2 routing with reference strings that are not real on-disk files.
    """
    monkeypatch.setenv("EXTRACTION_STUB_MODE", "true")

    result = _loader()("doi:10.1234/example", "doi")

    assert isinstance(result, str)
    assert result  # non-empty
    # The placeholder must contain at least one markdown heading so the
    # V2 ``SectionSegmenter`` step produces >=1 section (the same
    # invariant the legacy stub fixture relied on).
    assert "\n# " in result or result.startswith("# ")


def test_doi_source_type_outside_stub_mode_raises_not_implemented(
    monkeypatch,
):
    """Outside stub mode, an unresolvable DOI is an explicit
    ``NotImplementedError`` (not ``FileNotFoundError`` and not the
    generic ``ValueError`` V2 used to raise) so callers can branch on
    a documented migration path."""
    monkeypatch.delenv("EXTRACTION_STUB_MODE", raising=False)

    with pytest.raises(NotImplementedError) as exc_info:
        _loader()("doi:10.1234/example", "doi")

    msg = str(exc_info.value)
    assert "doi" in msg
    assert "stub" in msg or "process_literature" in msg


def test_url_source_type_raises_not_implemented():
    """``url`` is explicitly rejected with a migration path. Staging /
    prod traffic does not exercise this type yet, so we surface the
    gap rather than ship a half-working implementation."""
    with pytest.raises(NotImplementedError) as exc_info:
        _loader()("https://example.com/paper.pdf", "url")

    msg = str(exc_info.value)
    assert "url" in msg
    assert "process_literature" in msg or "migration" in msg.lower()


def test_datasource_source_type_raises_not_implemented():
    """``datasource`` (DataSource UUID) is explicitly rejected. The
    legacy V1 path loads ``content_md`` from the ``DataSource`` row;
    V2 needs that wiring before this contract can move."""
    with pytest.raises(NotImplementedError) as exc_info:
        _loader()("00000000-0000-0000-0000-000000000000", "datasource")

    msg = str(exc_info.value)
    assert "datasource" in msg


def test_unknown_source_type_raises_not_implemented():
    """Any unknown source type is rejected with the same error class
    so callers (including the API layer) can branch uniformly."""
    with pytest.raises(NotImplementedError) as exc_info:
        _loader()("anywhere", "arxiv")

    assert "arxiv" in str(exc_info.value)