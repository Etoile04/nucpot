"""Unit tests for stale_checkout_watchdog.py"""

# ruff: noqa: F841

import logging
import os
import unittest
from datetime import datetime, timedelta
try:  # py3.11+; fallback for the py3.9 CommandLineTools interpreter on the runner
    from datetime import UTC
except ImportError:  # pragma: no cover
    from datetime import timezone as _tz
    UTC = _tz.utc
from unittest.mock import MagicMock

from stale_checkout_watchdog import (
    EXIT_DRY_RUN_STALE,
    EXIT_ERROR,
    EXIT_SUCCESS,
    build_reap_comment,
    classify_issues,
    fetch_all_issues,
    is_stale,
    main,
    parse_issues_response,
    reap_stale_issue,
)


def _iso(minutes_ago: int) -> str:
    """Return an ISO-8601 timestamp `minutes_ago` minutes before now."""
    return (datetime.now(UTC) - timedelta(minutes=minutes_ago)).isoformat()


class TestIsStale(unittest.TestCase):
    """Test the is_stale timestamp comparison function."""

    def test_updated_40_min_ago_with_30_min_threshold_is_stale(self):
        self.assertTrue(is_stale(_iso(40), 30))

    def test_updated_29_min_ago_with_30_min_threshold_is_not_stale(self):
        self.assertFalse(is_stale(_iso(29), 30))

    def test_updated_exactly_at_boundary_minus_epsilon_is_not_stale(self):
        # Use 29.9 min to avoid microsecond race between _iso() and is_stale()
        ts = (datetime.now(UTC) - timedelta(minutes=29, seconds=54)).isoformat()
        self.assertFalse(is_stale(ts, 30))

    def test_updated_31_min_ago_is_stale(self):
        self.assertTrue(is_stale(_iso(31), 30))

    def test_custom_threshold_60_min(self):
        self.assertFalse(is_stale(_iso(59), 60))
        self.assertTrue(is_stale(_iso(61), 60))

    def test_future_updated_at_is_not_stale(self):
        future = (datetime.now(UTC) + timedelta(minutes=10)).isoformat()
        self.assertFalse(is_stale(future, 30))

    def test_threshold_zero_is_always_stale(self):
        self.assertTrue(is_stale(_iso(0), 0))


class TestParseIssuesResponse(unittest.TestCase):
    """Test parsing the paginated API response."""

    def test_parses_array_of_issues(self):
        data = [
            {
                "id": "abc",
                "identifier": "NFM-100",
                "status": "in_progress",
                "updatedAt": _iso(10),
                "checkoutRunId": "run-1",
            },
            {
                "id": "def",
                "identifier": "NFM-101",
                "status": "todo",
                "updatedAt": _iso(5),
                "checkoutRunId": None,
            },
        ]
        result = parse_issues_response(data)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["id"], "abc")

    def test_empty_array_returns_empty(self):
        result = parse_issues_response([])
        self.assertEqual(result, [])

    def test_extracts_id_and_checkout_run_id(self):
        data = [{"id": "x1", "checkoutRunId": "run-99", "updatedAt": _iso(1)}]
        result = parse_issues_response(data)
        self.assertEqual(result[0]["id"], "x1")
        self.assertEqual(result[0]["checkoutRunId"], "run-99")

    def test_handles_missing_checkout_run_id(self):
        data = [{"id": "x2", "updatedAt": _iso(1)}]
        result = parse_issues_response(data)
        self.assertIsNone(result[0]["checkoutRunId"])


class TestClassifyIssues(unittest.TestCase):
    """Test separating stale checkouts from active issues."""

    def test_no_stale_issues(self):
        issues = [
            {"id": "a", "checkoutRunId": "r1", "updatedAt": _iso(10)},
            {"id": "b", "checkoutRunId": "r2", "updatedAt": _iso(5)},
        ]
        stale, active = classify_issues(issues, 30)
        self.assertEqual(stale, [])
        self.assertEqual(len(active), 2)

    def test_identifies_stale_issue(self):
        issues = [
            {"id": "a", "checkoutRunId": "r1", "updatedAt": _iso(45)},
            {"id": "b", "checkoutRunId": "r2", "updatedAt": _iso(5)},
        ]
        stale, active = classify_issues(issues, 30)
        self.assertEqual(len(stale), 1)
        self.assertEqual(stale[0]["id"], "a")
        self.assertEqual(len(active), 1)

    def test_issues_without_checkout_run_are_active(self):
        issues = [
            {"id": "a", "checkoutRunId": None, "updatedAt": _iso(120)},
            {"id": "b", "checkoutRunId": "", "updatedAt": _iso(200)},
        ]
        stale, active = classify_issues(issues, 30)
        self.assertEqual(stale, [])
        self.assertEqual(len(active), 2)

    def test_empty_list(self):
        stale, active = classify_issues([], 30)
        self.assertEqual(stale, [])
        self.assertEqual(active, [])

    def test_all_stale(self):
        issues = [
            {"id": "a", "checkoutRunId": "r1", "updatedAt": _iso(100)},
            {"id": "b", "checkoutRunId": "r2", "updatedAt": _iso(200)},
        ]
        stale, active = classify_issues(issues, 30)
        self.assertEqual(len(stale), 2)
        self.assertEqual(active, [])


class TestBuildReapComment(unittest.TestCase):
    """Test building the reap comment payload."""

    def test_contains_required_fields(self):
        comment = build_reap_comment("issue-123", "run-abc")
        self.assertIn("[STALE-CHECKOUT-REAPED]", comment)
        self.assertIn("run-abc", comment)
        self.assertIn("issue-123", comment)

    def test_contains_timestamp(self):
        comment = build_reap_comment("issue-123", "run-abc")
        self.assertIn("2026", comment)

    def test_is_plain_string(self):
        comment = build_reap_comment("issue-123", "run-abc")
        self.assertIsInstance(comment, str)


class TestFetchAllIssues(unittest.TestCase):
    """Test paginated fetching of all issues."""

    def test_single_page(self):
        mock_api = MagicMock()
        mock_api.get.return_value = [
            {"id": "a", "checkoutRunId": "r1", "updatedAt": _iso(5)},
        ]
        result = fetch_all_issues(mock_api, "company-1")
        self.assertEqual(len(result), 1)
        mock_api.get.assert_called_once()

    def test_multiple_pages(self):
        mock_api = MagicMock()
        page1 = [
            {"id": f"i-{i}", "checkoutRunId": f"r{i}", "updatedAt": _iso(i)} for i in range(100)
        ]
        page2 = [{"id": "last", "checkoutRunId": "r-last", "updatedAt": _iso(5)}]
        mock_api.get.side_effect = [page1, page2]
        result = fetch_all_issues(mock_api, "company-1", page_size=100)
        self.assertEqual(len(result), 101)
        self.assertEqual(mock_api.get.call_count, 2)

    def test_empty_first_page(self):
        mock_api = MagicMock()
        mock_api.get.return_value = []
        result = fetch_all_issues(mock_api, "company-1")
        self.assertEqual(result, [])
        mock_api.get.assert_called_once()

    def test_passes_offset_correctly(self):
        mock_api = MagicMock()
        page1 = [
            {"id": f"i-{i}", "checkoutRunId": f"r{i}", "updatedAt": _iso(i)} for i in range(10)
        ]
        page2 = [{"id": "i-last", "checkoutRunId": "r-last", "updatedAt": _iso(99)}]
        mock_api.get.side_effect = [page1, page2]
        fetch_all_issues(mock_api, "company-1", page_size=10)
        calls = mock_api.get.call_args_list
        # args are (path, query_dict) — query is passed as keyword
        self.assertEqual(calls[0].kwargs.get("query", {}).get("offset"), 0)
        self.assertEqual(calls[1].kwargs.get("query", {}).get("offset"), 10)


class TestReapStaleIssue(unittest.TestCase):
    """Test reaping a single stale issue (release + comment)."""

    def test_release_and_comment_called(self):
        mock_api = MagicMock()
        mock_api.post.return_value = {"status": "ok"}
        issue = {
            "id": "issue-1",
            "identifier": "NFM-100",
            "checkoutRunId": "run-1",
            "updatedAt": _iso(60),
        }
        reap_stale_issue(mock_api, issue)
        self.assertEqual(mock_api.post.call_count, 2)

    def test_release_403_skips_comment(self):
        mock_api = MagicMock()
        mock_api.post.side_effect = [
            PermissionError("403 - already handled"),
        ]
        issue = {
            "id": "issue-1",
            "identifier": "NFM-100",
            "checkoutRunId": "run-1",
            "updatedAt": _iso(60),
        }
        reap_stale_issue(mock_api, issue)
        self.assertEqual(mock_api.post.call_count, 1)

    def test_release_returns_true_on_success(self):
        mock_api = MagicMock()
        mock_api.post.return_value = {"status": "ok"}
        issue = {
            "id": "issue-1",
            "identifier": "NFM-100",
            "checkoutRunId": "run-1",
            "updatedAt": _iso(60),
        }
        result = reap_stale_issue(mock_api, issue)
        self.assertTrue(result)

    def test_release_returns_false_on_403(self):
        mock_api = MagicMock()
        mock_api.post.side_effect = [PermissionError("403")]
        issue = {
            "id": "issue-1",
            "identifier": "NFM-100",
            "checkoutRunId": "run-1",
            "updatedAt": _iso(60),
        }
        result = reap_stale_issue(mock_api, issue)
        self.assertFalse(result)


class TestMain(unittest.TestCase):
    """Test the main entry point with mocked API."""

    def _make_args(self, **overrides):
        defaults = {
            "dry_run": False,
            "stale_threshold_minutes": 30,
            "verbose": False,
        }
        defaults.update(overrides)
        return type("Args", (), defaults)()

    def test_dry_run_no_mutations(self):
        mock_api = MagicMock()
        mock_api.get.return_value = [
            {
                "id": "s1",
                "identifier": "NFM-100",
                "checkoutRunId": "run-stale",
                "updatedAt": _iso(60),
            },
        ]
        args = self._make_args(dry_run=True)
        exit_code = main(args, api_client=mock_api, company_id="c1")
        self.assertEqual(exit_code, EXIT_DRY_RUN_STALE)
        mock_api.post.assert_not_called()

    def test_dry_run_no_stale_returns_zero(self):
        mock_api = MagicMock()
        mock_api.get.return_value = [
            {
                "id": "a1",
                "identifier": "NFM-100",
                "checkoutRunId": "run-fresh",
                "updatedAt": _iso(5),
            },
        ]
        args = self._make_args(dry_run=True)
        exit_code = main(args, api_client=mock_api, company_id="c1")
        self.assertEqual(exit_code, EXIT_SUCCESS)
        mock_api.post.assert_not_called()

    def test_live_mode_reaps_stale(self):
        mock_api = MagicMock()
        mock_api.get.return_value = [
            {
                "id": "s1",
                "identifier": "NFM-100",
                "checkoutRunId": "run-stale",
                "updatedAt": _iso(60),
            },
        ]
        mock_api.post.return_value = {"status": "ok"}
        args = self._make_args(dry_run=False)
        exit_code = main(args, api_client=mock_api, company_id="c1")
        self.assertEqual(exit_code, EXIT_SUCCESS)
        self.assertGreaterEqual(mock_api.post.call_count, 1)

    def test_live_mode_no_stale_returns_zero(self):
        mock_api = MagicMock()
        mock_api.get.return_value = [
            {
                "id": "a1",
                "identifier": "NFM-100",
                "checkoutRunId": "run-fresh",
                "updatedAt": _iso(5),
            },
        ]
        args = self._make_args(dry_run=False)
        exit_code = main(args, api_client=mock_api, company_id="c1")
        self.assertEqual(exit_code, EXIT_SUCCESS)
        mock_api.post.assert_not_called()

    def test_api_error_returns_one(self):
        mock_api = MagicMock()
        mock_api.get.side_effect = RuntimeError("network error")
        args = self._make_args()
        exit_code = main(args, api_client=mock_api, company_id="c1")
        self.assertEqual(exit_code, EXIT_ERROR)

    def test_verbose_enables_debug_logging(self):
        mock_api = MagicMock()
        mock_api.get.return_value = []
        args = self._make_args(verbose=True)
        # Reset both root and module loggers so basicConfig takes effect
        root = logging.getLogger()
        root.handlers.clear()
        root.setLevel(logging.NOTSET)
        logger = logging.getLogger("stale_checkout_watchdog")
        logger.handlers.clear()
        logger.setLevel(logging.NOTSET)
        logger.propagate = True
        with self.assertLogs("stale_checkout_watchdog", level=logging.DEBUG):
            main(args, api_client=mock_api, company_id="c1")

    def test_idempotent_multiple_runs(self):
        """Running twice with same stale issue: first reaps, second finds no stale."""
        mock_api = MagicMock()
        mock_api.get.return_value = [
            {
                "id": "s1",
                "identifier": "NFM-100",
                "checkoutRunId": "run-stale",
                "updatedAt": _iso(60),
            },
        ]
        mock_api.post.return_value = {"status": "ok"}
        args = self._make_args(dry_run=False)

        # First run: reaps the stale issue
        exit_code_1 = main(args, api_client=mock_api, company_id="c1")
        self.assertEqual(exit_code_1, EXIT_SUCCESS)
        post_count_1 = mock_api.post.call_count

        # Second run: issue now has no checkoutRunId (reaped)
        mock_api.get.return_value = [
            {"id": "s1", "identifier": "NFM-100", "checkoutRunId": None, "updatedAt": _iso(60)},
        ]
        exit_code_2 = main(args, api_client=mock_api, company_id="c1")
        self.assertEqual(exit_code_2, EXIT_SUCCESS)
        # No new mutations on second run
        self.assertEqual(mock_api.post.call_count, post_count_1)

    def test_custom_threshold(self):
        mock_api = MagicMock()
        mock_api.get.return_value = [
            {
                "id": "s1",
                "identifier": "NFM-100",
                "checkoutRunId": "run-stale",
                "updatedAt": _iso(45),
            },
        ]
        args = self._make_args(dry_run=True, stale_threshold_minutes=60)
        exit_code = main(args, api_client=mock_api, company_id="c1")
        # 45 min is not stale with 60 min threshold
        self.assertEqual(exit_code, EXIT_SUCCESS)

    def test_reap_error_does_not_abort_scan(self):
        """A single reap failure must not prevent reaping other stale issues."""
        mock_api = MagicMock()
        mock_api.get.return_value = [
            {"id": "s1", "identifier": "NFM-100", "checkoutRunId": "run-1", "updatedAt": _iso(60)},
            {"id": "s2", "identifier": "NFM-101", "checkoutRunId": "run-2", "updatedAt": _iso(90)},
        ]
        mock_api.post.side_effect = [
            {"status": "ok"},  # release s1
            {"status": "ok"},  # comment s1
            RuntimeError("boom"),  # release s2 fails
        ]
        args = self._make_args(dry_run=False)
        exit_code = main(args, api_client=mock_api, company_id="c1")
        # Should still succeed overall (best-effort), not EXIT_ERROR
        self.assertEqual(exit_code, EXIT_SUCCESS)


class TestMissingEnvVars(unittest.TestCase):
    """Test that missing env vars produce EXIT_ERROR."""

    def test_missing_api_url_returns_error(self):
        mock_api = MagicMock()
        args = type(
            "Args", (), {"dry_run": False, "stale_threshold_minutes": 30, "verbose": False}
        )()
        for var in ("PAPERCLIP_API_URL", "PAPERCLIP_API_KEY", "PAPERCLIP_COMPANY_ID"):
            os.environ.pop(var, None)
        exit_code = main(args, api_client=mock_api, company_id="")
        self.assertEqual(exit_code, EXIT_ERROR)

    def test_missing_api_key_returns_error(self):
        mock_api = MagicMock()
        args = type(
            "Args", (), {"dry_run": False, "stale_threshold_minutes": 30, "verbose": False}
        )()
        os.environ["PAPERCLIP_API_URL"] = "http://localhost:1234"
        os.environ.pop("PAPERCLIP_API_KEY", None)
        os.environ.pop("PAPERCLIP_COMPANY_ID", None)
        exit_code = main(args, api_client=mock_api, company_id="")
        self.assertEqual(exit_code, EXIT_ERROR)

    def teardown(self):
        for var in ("PAPERCLIP_API_URL", "PAPERCLIP_API_KEY", "PAPERCLIP_COMPANY_ID"):
            os.environ.pop(var, None)


class TestIsStaleEdgeCases(unittest.TestCase):
    """Test edge cases for is_stale."""

    def test_none_updated_at_treated_as_stale(self):
        self.assertTrue(is_stale(None, 30))

    def test_empty_string_updated_at_treated_as_stale(self):
        self.assertTrue(is_stale("", 30))

    def test_naive_datetime_gets_utc_timezone(self):
        # Naive ISO string (no Z suffix) — should be treated as UTC
        naive = (datetime.now(UTC) - timedelta(minutes=60)).strftime("%Y-%m-%dT%H:%M:%S")
        self.assertTrue(is_stale(naive, 30))


class TestReapCommentBodyShape(unittest.TestCase):
    """Verify the comment body dict shape matches API requirements."""

    def test_body_not_comment_key(self):
        """The script must POST {body: ...} not {comment: ...}."""
        # Verify build_reap_comment returns a plain string
        # and that reap_stale_issue posts with {"body": comment_text}
        mock_api = MagicMock()
        mock_api.post.return_value = {"status": "ok"}
        issue = {"id": "x", "identifier": "NFM-999", "checkoutRunId": "r1", "updatedAt": _iso(60)}
        reap_stale_issue(mock_api, issue)
        # Second call (comment) should use {"body": ...}
        comment_call = mock_api.post.call_args_list[1]
        body_arg = (
            comment_call.kwargs.get("body") or comment_call[0][1]
            if len(comment_call[0]) > 1
            else comment_call.kwargs.get("body")
        )
        self.assertIn("body", comment_call.kwargs if comment_call.kwargs else {})
        comment_payload = comment_call.kwargs["body"]
        self.assertIn("[STALE-CHECKOUT-REAPED]", comment_payload["body"])


class TestExitCodes(unittest.TestCase):
    """Verify exit code constants."""

    def test_success_is_zero(self):
        self.assertEqual(EXIT_SUCCESS, 0)

    def test_error_is_one(self):
        self.assertEqual(EXIT_ERROR, 1)

    def test_dry_run_stale_is_two(self):
        self.assertEqual(EXIT_DRY_RUN_STALE, 2)


if __name__ == "__main__":
    unittest.main()
