"""Tests for ``load_v2_content`` (NFM-2909).

Strangler-fig content-loading contract for the V2 pipeline::

    file,
    internal_id,
    ""           → V1-compatible file-path semantics: try
                   *source_reference* on disk; if missing and
                   ``EXTRACTION_STUB_MODE`` is on, fall back to the
                   placeholder markdown so the chunker and the
                   ``ontofuel_extract`` stub path can run end-to-end
                   in CI / dev. Missing file outside stub mode
                   raises ``FileNotFoundError``.
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

The loader is the single content-resolution point for both V2 entry
paths — the dispatcher in ``extraction_pipeline_dispatch`` and the
legacy ``trigger_extraction`` V2 branch in ``extraction_pipeline``.
Tested in isolation so the contract is locked before the
``EXTRACTION_PIPELINE_V2`` flag flip to default-True (NFM-2869).
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_stub_mode(monkeypatch):
    """Default each case to a known stub-mode state.

    ``load_v2_content`` consults ``EXTRACTION_STUB_MODE`` AND falls
    back to placeholder content when ``LLM_API_KEY`` is unset (V1
    compatibility: V1 ``ontofuel_extract`` falls back to stub when
    the LLM is not configured). Tests that want a missing-file
    error path must set ``LLM_API_KEY`` so the no-LLM fallback
    doesn't silently shadow their assertion.
    """
    monkeypatch.delenv("EXTRACTION_STUB_MODE", raising=False)
    monkeypatch.setenv("LLM_API_KEY", "test-key-for-loader-tests")


def _loader():
    from nfm_db.services.extraction_pipeline_dispatch import (
        load_v2_content,
    )

    return load_v2_content


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


def test_internal_id_source_type_with_local_file(tmp_path):
    """``source_type='internal_id'`` is V1-compatible file-path
    semantics — V1 ``ontofuel_extract`` falls through to "file
    path on disk" for everything except ``doi`` / ``datasource``,
    so the V2 loader must too (NFM-2909 review feedback: previously
    rejected with NotImplementedError, breaking V1 parity)."""
    sample = tmp_path / "internal.md"
    sample.write_text("# Internal\n\nCO2 properties.", encoding="utf-8")

    result = _loader()(str(sample), "internal_id")

    assert result == "# Internal\n\nCO2 properties."


def test_internal_id_source_type_stub_mode_returns_placeholder(monkeypatch):
    """Missing ``internal_id`` reference in stub mode returns the
    placeholder markdown so the 5-step orchestrator can complete."""
    monkeypatch.setenv("EXTRACTION_STUB_MODE", "true")

    result = _loader()("missing-internal-id", "internal_id")

    assert isinstance(result, str)
    assert result  # non-empty placeholder


def test_empty_source_type_stub_mode_returns_placeholder(monkeypatch):
    """Empty ``source_type`` (one of the live staging jobs) follows
    file-path semantics; missing source in stub mode returns the
    placeholder so V2 doesn't regress staging traffic (NFM-2909
    review feedback)."""
    monkeypatch.setenv("EXTRACTION_STUB_MODE", "true")

    # Empty source_type with empty source_reference.
    result = _loader()("", "")

    assert isinstance(result, str)
    assert result  # non-empty placeholder


def test_file_source_type_stub_mode_returns_placeholder(monkeypatch, tmp_path):
    """``source_type='file'`` with a missing path falls back to the
    placeholder markdown when stub mode is on. This mirrors the V1
    ``ontofuel_extract`` behaviour where ``EXTRACTION_STUB_MODE=true``
    bypasses the file read and returns 3 demo records — the V2
    chunker needs SOMETHING to chunk, so we provide placeholder
    content instead of crashing the orchestrator.
    """
    monkeypatch.setenv("EXTRACTION_STUB_MODE", "true")
    missing = tmp_path / "absent.md"

    result = _loader()(str(missing), "file")

    assert isinstance(result, str)
    assert result  # non-empty placeholder
    assert "\n# " in result or result.startswith("# ")
