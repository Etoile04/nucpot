"""Regression tests for NFM-2443 — silent-failure fix for scripts/okr.

The defect: ``scripts/okr`` reads Paperclip issues but never sends an
``Authorization`` header. The call always 401s, fetch_all_issues returns an
empty list (or breaks early), and every referenced issue resolves as
``"unknown"``. KR-1's commitEfficiency then computes ``0 / N = 0.000``, which
is the exact silent-wrong-number the defect calls out.

AC1: fetch_all_issues (and fetch_issue_statuses) send ``Authorization: Bearer``
    from ``$PAPERCLIP_API_KEY``; missing key raises rather than proceeding
    unauthenticated.
AC2: ``--api-url`` resolves from ``$PAPERCLIP_API_URL`` in main();
    argparse default is ``None`` — no localhost fallback (NFM-2444).
AC3: A failed issue fetch makes KR-1 report ``[no data]``. It must be
    impossible for a fetch failure to render as a numeric KR value.
AC4: Regression test covering AC3 — inject a failing fetch, assert KR-1
    is ``[no data]`` and not ``0.000``.
"""

# ruff: noqa: RUF012, SIM118

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import MagicMock, patch

import pytest

from scripts.okr import PaperclipFetchError, fetch_all_issues
from scripts.okr.commit_efficiency import fetch_issue_statuses
from scripts.okr.report import build_arg_parser
from scripts.okr.report import main as report_main

# ---------------------------------------------------------------------------
# AC1 — Authorization header sent; missing key raises; HTTP error raises
# ---------------------------------------------------------------------------


class _HeaderCaptureHandler(BaseHTTPRequestHandler):
    """Captures every header the client sends so tests can assert on them."""

    captured: dict[str, str] = {}

    def do_GET(self) -> None:
        for header_name in self.headers.keys():
            value = self.headers.get(header_name)
            if value is not None:
                _HeaderCaptureHandler.captured[header_name] = value

        body = json.dumps([]).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        """Silence request logs during tests."""
        pass


@pytest.mark.unit
def test_fetch_all_issues_sends_authorization_header() -> None:
    """fetch_all_issues must attach ``Authorization: Bearer <key>``."""
    _HeaderCaptureHandler.captured = {}
    server = HTTPServer(("127.0.0.1", 0), _HeaderCaptureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        fetch_all_issues(
            f"http://127.0.0.1:{port}",
            company_id="co-1",
            params={},
            api_key="secret-key-123",
        )
        assert _HeaderCaptureHandler.captured.get("Authorization") == "Bearer secret-key-123", (
            f"Authorization header missing or wrong: {_HeaderCaptureHandler.captured}"
        )
    finally:
        server.shutdown()


@pytest.mark.unit
def test_fetch_all_issues_raises_when_api_key_empty() -> None:
    """An empty api_key must NOT proceed unauthenticated — raise instead."""
    with pytest.raises(PaperclipFetchError, match=r"API key"):
        fetch_all_issues(
            "http://localhost:3000",
            company_id="co-1",
            params={},
            api_key="",
        )


@pytest.mark.unit
def test_fetch_all_issues_raises_when_api_key_is_none() -> None:
    """A None api_key must NOT proceed unauthenticated — raise instead."""
    with pytest.raises(PaperclipFetchError, match=r"API key"):
        fetch_all_issues(
            "http://localhost:3000",
            company_id="co-1",
            params={},
            api_key=None,
        )


@pytest.mark.unit
def test_fetch_all_issues_raises_on_http_error() -> None:
    """A 401/404/etc. from Paperclip must surface as PaperclipFetchError,
    not as a partial result list."""
    import urllib.error

    with patch("scripts.okr.urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.side_effect = urllib.error.URLError("401 Unauthorized")
        with pytest.raises(PaperclipFetchError, match=r"HTTP"):
            fetch_all_issues(
                "http://localhost:3000",
                company_id="co-1",
                params={},
                api_key="secret-key",
            )


@pytest.mark.unit
def test_fetch_all_issues_wraps_timeout_error() -> None:
    """Socket timeouts must degrade through the same fetch error contract."""
    with (
        patch(
            "scripts.okr.urllib.request.urlopen",
            side_effect=TimeoutError("timed out"),
        ),
        pytest.raises(PaperclipFetchError, match=r"HTTP error"),
    ):
        fetch_all_issues(
            "http://localhost:3000",
            company_id="co-1",
            params={},
            api_key="secret-key",
        )


@pytest.mark.unit
def test_fetch_issue_statuses_propagates_fetch_error() -> None:
    """fetch_issue_statuses must NOT swallow PaperclipFetchError — the
    downstream report needs to know the fetch failed."""
    with (
        patch(
            "scripts.okr.commit_efficiency.fetch_all_issues",
            side_effect=PaperclipFetchError("simulated 401"),
        ),
        pytest.raises(PaperclipFetchError),
    ):
        fetch_issue_statuses(
            ["NFM-100"],
            "http://localhost:3000",
            "co-1",
            api_key="secret-key",
        )


# ---------------------------------------------------------------------------
# AC2 — --api-url default from $PAPERCLIP_API_URL; None when unset (no fallback)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_default_api_url_from_env_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """Argparse default is None; main() resolves $PAPERCLIP_API_URL.

    NFM-2442/NFM-2444: argparse no longer reads the env var inline.
    The resolution args.api_url or os.environ["PAPERCLIP_API_URL"] lives
    in main(), so argparse itself always yields None unless --api-url
    is passed explicitly.
    """
    monkeypatch.setenv("PAPERCLIP_API_URL", "https://paperclip.example.com")
    args = build_arg_parser().parse_args(["--since", "2026-07-27", "--until", "2026-08-02"])
    assert args.api_url is None  # argparse default; main() does the env lookup


@pytest.mark.unit
def test_default_api_url_is_none_when_env_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With $PAPERCLIP_API_URL unset, --api-url is None (no localhost fallback).

    NFM-2442/NFM-2444: the script must not guess a host — main() will error
    out before any network call. The argparse default is explicitly None.
    """
    monkeypatch.delenv("PAPERCLIP_API_URL", raising=False)
    args = build_arg_parser().parse_args(["--since", "2026-07-27", "--until", "2026-08-02"])
    assert args.api_url is None


@pytest.mark.unit
def test_explicit_api_url_overrides_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """An explicit --api-url beats the env var."""
    monkeypatch.setenv("PAPERCLIP_API_URL", "https://paperclip.example.com")
    args = build_arg_parser().parse_args(
        [
            "--since",
            "2026-07-27",
            "--until",
            "2026-08-02",
            "--api-url",
            "http://override.example.com",
        ]
    )
    assert args.api_url == "http://override.example.com"


# ---------------------------------------------------------------------------
# AC3 + AC4 — KR-1 must render as [no data] on fetch failure, NOT 0.000
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_kr1_is_no_data_when_fetch_fails(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """AC3+AC4: inject a failing fetch and assert KR-1 reports [no data].

    This is the regression that NFM-2443 was filed for: the old behaviour
    returned an empty issue list, every ref resolved as ``"unknown"``, and
    KR-1 silently rendered as ``0.000``. The fix must short-circuit to
    ``[no data]`` so a reader cannot confuse "we measured zero" with "the
    measurement never ran".
    """
    monkeypatch.setenv("PAPERCLIP_COMPANY_ID", "co-1")
    monkeypatch.setenv("PAPERCLIP_API_KEY", "secret-key")

    # Two referenced issues that, if the fetch succeeded, would resolve to
    # known statuses. We mock the fetch to fail so we exercise the failure
    # path.
    mock_git_log = MagicMock(
        return_value=("abc1234 feat: NFM-100 login\ndef5678 feat: NFM-101 logout\n")
    )

    fetch_failure = PaperclipFetchError("simulated 401 Unauthorized")

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

    with (
        patch("scripts.okr.report.run_git_log", mock_git_log),
        patch(
            "scripts.okr.report.fetch_issue_statuses",
            side_effect=fetch_failure,
        ),
        patch(
            "scripts.okr.report.fetch_all_issues",
            side_effect=fetch_failure,
        ),
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

    # AC4 (the precise assertion the defect calls for):
    # find the KR-1 row and verify it says [no data], not 0.000
    kr1_line = next(
        (line for line in out.splitlines() if line.startswith("KR-1")),
        None,
    )
    assert kr1_line is not None, f"KR-1 line not found in output:\n{out}"
    assert "[no data]" in kr1_line, (
        f"KR-1 must render as [no data] on fetch failure, got: {kr1_line!r}"
    )
    assert "0.000" not in kr1_line, (
        f"KR-1 must NEVER render as 0.000 on fetch failure: {kr1_line!r}"
    )

    # KR-4 must also degrade honestly to [no data] (same fetch path).
    kr4_line = next(
        (line for line in out.splitlines() if line.startswith("KR-4")),
        None,
    )
    assert kr4_line is not None, f"KR-4 line not found in output:\n{out}"
    assert "[no data]" in kr4_line, (
        f"KR-4 must render as [no data] on fetch failure, got: {kr4_line!r}"
    )


@pytest.mark.unit
def test_kr1_uses_real_values_when_fetch_succeeds(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Companion test for AC3 — the happy path must still work end-to-end."""
    monkeypatch.setenv("PAPERCLIP_COMPANY_ID", "co-1")
    monkeypatch.setenv("PAPERCLIP_API_KEY", "secret-key")

    # Two commits; both reference NFM-100 which is "done".
    mock_git_log = MagicMock(
        return_value=("abc1234 feat: NFM-100 login\ndef5678 feat: NFM-100 logout\n")
    )

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

    with (
        patch("scripts.okr.report.run_git_log", mock_git_log),
        patch(
            "scripts.okr.report.fetch_issue_statuses",
            return_value={"NFM-100": "done"},
        ),
        patch(
            "scripts.okr.report.fetch_all_issues",
            return_value=[],
        ),
        patch(
            "scripts.okr.report.compute_kr3",
            return_value={
                "key": "deploy_first_pass_success",
                "value": 0.90,
                "unit": "ratio",
            },
        ),
        patch(
            "scripts.okr.report.compute_kr5",
            return_value={
                "key": "test_coverage",
                "value": 0.75,
                "unit": "ratio",
            },
        ),
    ):
        report_main()

    out = capsys.readouterr().out
    # 1 done issue out of 2 commits = 0.5 commitEfficiency
    assert "0.500" in out, f"KR-1 happy path must render 0.500 for 1/2 done, got:\n{out}"
