"""Regression tests for NFM-2492 — empty-but-successful fetch must not
render KR-1 as ``0.000``.

NFM-2443 closed the *fetch-failure* hole: a raised ``PaperclipFetchError``
degrades KR-1/KR-2/KR-4 to ``[no data]``. But an **empty yet successful**
fetch (HTTP 200 with ``[]`` — wrong ``company_id``, a token scoped to no
issues, or a lookup-key mismatch like NFM-2446's ``key``-vs-``identifier``
bug) raises nothing. Every ref then falls through to ``"unknown"`` →
"not done" → a clean numeric ``0.000``, which is the exact
reader-cannot-distinguish failure ADR-008 / NFM-2036 were written about,
reached by a different route.

AC1: An empty issue list with a non-empty ``issue_refs`` renders KR-1/KR-2
    as ``[no data]``. A genuinely empty commit range (no refs at all) stays
    distinguishable and keeps rendering a real number.
AC2: A non-empty issue list against which *zero* refs resolve is the same
    class of failure (lookup mismatch) and degrades identically.
AC3: End-to-end regression: the rendered KR-1 line contains ``[no data]``
    and not ``0.000`` for the empty-200 case, mirroring
    ``test_kr1_is_no_data_when_fetch_fails``.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from scripts.okr import PaperclipEmptyResultError, PaperclipFetchError
from scripts.okr.commit_efficiency import fetch_issue_statuses
from scripts.okr.report import main as report_main

# ---------------------------------------------------------------------------
# Exception contract — the new error must flow through existing guards
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_empty_result_error_is_a_fetch_error() -> None:
    """``PaperclipEmptyResultError`` must subclass ``PaperclipFetchError``.

    Every existing ``except PaperclipFetchError`` guard (report.py KR-1/KR-2
    and KR-4) then degrades to ``[no data]`` without duplicating the handler,
    while callers that care can still tell "the call failed" apart from "the
    call succeeded and resolved nothing".
    """
    assert issubclass(PaperclipEmptyResultError, PaperclipFetchError)


# ---------------------------------------------------------------------------
# AC1 — empty issue list + non-empty refs
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_empty_issue_list_with_refs_raises() -> None:
    """AC1: HTTP 200 with ``[]`` while refs exist is not a measurement."""
    with (
        patch(
            "scripts.okr.commit_efficiency.fetch_all_issues",
            return_value=[],
        ),
        pytest.raises(PaperclipEmptyResultError, match=r"0 issues"),
    ):
        fetch_issue_statuses(
            ["NFM-100", "NFM-200"],
            "http://localhost:3000",
            "co-1",
            api_key="secret-key",
        )


@pytest.mark.unit
def test_no_refs_still_returns_empty_map_without_raising() -> None:
    """AC1 boundary: a genuinely empty commit range is a real measurement.

    No refs means nothing was looked up, so nothing can have gone wrong with
    the lookup. This must stay distinguishable from the empty-200 case — it
    keeps rendering a number, not ``[no data]``.
    """
    with patch(
        "scripts.okr.commit_efficiency.fetch_all_issues",
        return_value=[],
    ) as mock_fetch:
        assert (
            fetch_issue_statuses(
                [],
                "http://localhost:3000",
                "co-1",
                api_key="secret-key",
            )
            == {}
        )
        mock_fetch.assert_not_called()


# ---------------------------------------------------------------------------
# AC2 — non-empty list, zero refs resolved (lookup mismatch)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_zero_refs_resolved_against_non_empty_list_raises() -> None:
    """AC2: this is the class NFM-2446's ``key``-vs-``identifier`` bug fell in.

    2438 issues came back and not one ref matched — that is a lookup mismatch,
    not a team that shipped nothing.
    """
    with (
        patch(
            "scripts.okr.commit_efficiency.fetch_all_issues",
            return_value=[
                {"identifier": None, "key": "NFM-100", "status": "done"},
                {"identifier": None, "key": "NFM-200", "status": "done"},
            ],
        ),
        pytest.raises(PaperclipEmptyResultError, match=r"none.*resolved"),
    ):
        fetch_issue_statuses(
            ["NFM-100", "NFM-200"],
            "http://localhost:3000",
            "co-1",
            api_key="secret-key",
        )


@pytest.mark.unit
def test_partial_resolution_does_not_raise() -> None:
    """AC2 boundary: one resolved ref proves the lookup works.

    The remaining unresolved refs stay ``"unknown"`` — a real signal about
    those issues, not evidence of a broken fetch.
    """
    with patch(
        "scripts.okr.commit_efficiency.fetch_all_issues",
        return_value=[{"identifier": "NFM-100", "status": "done"}],
    ):
        result = fetch_issue_statuses(
            ["NFM-100", "NFM-999"],
            "http://localhost:3000",
            "co-1",
            api_key="secret-key",
        )
    assert result == {"NFM-100": "done", "NFM-999": "unknown"}


# ---------------------------------------------------------------------------
# AC3 — end-to-end rendered output
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_kr1_is_no_data_when_fetch_returns_empty_200(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """AC3: the exact reproduction from the defect report.

    ``fetch_all_issues`` returns ``[]`` without raising. Pre-fix, KR-1
    rendered ``0.000``; the reader could not tell that from a real zero.
    """
    monkeypatch.setenv("PAPERCLIP_COMPANY_ID", "co-1")
    monkeypatch.setenv("PAPERCLIP_API_KEY", "secret-key")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "report",
            "--since",
            "2026-07-27",
            "--until",
            "2026-08-02",
        ],
    )

    mock_git_log = MagicMock(return_value=("abc1234 fix: x (NFM-100)\ndef5678 fix: y (NFM-200)\n"))

    with (
        patch("scripts.okr.report.run_git_log", mock_git_log),
        # HTTP 200, empty body — no exception raised anywhere.
        patch("scripts.okr.commit_efficiency.fetch_all_issues", return_value=[]),
        patch("scripts.okr.report.fetch_all_issues", return_value=[]),
        patch(
            "scripts.okr.report.compute_kr3",
            return_value={
                "key": "deploy_first_pass_success",
                "value": None,
                "status": "no_data_baseline",
            },
        ),
        patch(
            "scripts.okr.report.compute_kr5",
            return_value={
                "key": "test_coverage",
                "value": None,
                "status": "no_data_baseline",
            },
        ),
    ):
        report_main()

    out = capsys.readouterr().out

    kr1_line = next(
        (line for line in out.splitlines() if line.startswith("KR-1")),
        None,
    )
    assert kr1_line is not None, f"KR-1 line not found in output:\n{out}"
    assert "[no data]" in kr1_line, (
        f"KR-1 must render as [no data] on an empty 200, got: {kr1_line!r}"
    )
    assert "0.000" not in kr1_line, f"KR-1 must NEVER render as 0.000 on an empty 200: {kr1_line!r}"

    kr2_line = next(
        (line for line in out.splitlines() if line.startswith("KR-2")),
        None,
    )
    assert kr2_line is not None, f"KR-2 line not found in output:\n{out}"
    assert "[no data]" in kr2_line, f"KR-2 must degrade with KR-1, got: {kr2_line!r}"


@pytest.mark.unit
def test_kr1_still_numeric_for_commit_range_with_no_refs(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """AC1 boundary, end-to-end: commits that reference no issues.

    ``commitEfficiency = 0/2`` and ``structuralWasteRate = 2/2`` are honest
    numbers here — nothing was looked up, so nothing could have failed. This
    guards against the fix over-reaching into ``[no data]`` for real zeros.
    """
    monkeypatch.setenv("PAPERCLIP_COMPANY_ID", "co-1")
    monkeypatch.setenv("PAPERCLIP_API_KEY", "secret-key")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "report",
            "--since",
            "2026-07-27",
            "--until",
            "2026-08-02",
        ],
    )

    mock_git_log = MagicMock(return_value=("abc1234 chore: bump deps\ndef5678 docs: fix typo\n"))

    with (
        patch("scripts.okr.report.run_git_log", mock_git_log),
        patch("scripts.okr.commit_efficiency.fetch_all_issues", return_value=[]),
        patch("scripts.okr.report.fetch_all_issues", return_value=[]),
        patch(
            "scripts.okr.report.compute_kr3",
            return_value={
                "key": "deploy_first_pass_success",
                "value": None,
                "status": "no_data_baseline",
            },
        ),
        patch(
            "scripts.okr.report.compute_kr5",
            return_value={
                "key": "test_coverage",
                "value": None,
                "status": "no_data_baseline",
            },
        ),
    ):
        report_main()

    out = capsys.readouterr().out

    kr1_line = next(
        (line for line in out.splitlines() if line.startswith("KR-1")),
        None,
    )
    assert kr1_line is not None, f"KR-1 line not found in output:\n{out}"
    assert "0.000" in kr1_line, (
        "A commit range with zero issue refs is a real measurement and must "
        f"keep rendering a number, got: {kr1_line!r}"
    )

    kr2_line = next(
        (line for line in out.splitlines() if line.startswith("KR-2")),
        None,
    )
    assert kr2_line is not None, f"KR-2 line not found in output:\n{out}"
    assert "1.000" in kr2_line, (
        f"Waste rate for 2/2 unreferenced commits must be 1.000: {kr2_line!r}"
    )
