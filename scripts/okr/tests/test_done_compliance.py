"""Tests for done_compliance.py — Done standard compliance check hook.

Tests use mocked Paperclip API responses to verify each role's check logic.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from scripts.okr.done_compliance import (
    ComplianceCheck,
    ComplianceReport,
    build_report,
    check_children_complete,
    check_cpo_accepted,
    check_acceptance_criteria_defined,
    check_ci_green,
    check_code_review_approved,
    check_coverage_ge_80,
    check_staging_verified,
    check_tech_docs_updated,
    check_tests_passing,
    detect_role,
    fetch_issue,
    fetch_issue_comments,
    fetch_child_issues,
    parse_args,
    ROLE_CHECKS,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def base_url() -> str:
    return "https://paperclip.example.com"


@pytest.fixture
def api_key() -> str:
    return "test-api-key"


@pytest.fixture
def sample_issue() -> dict:
    """A typical issue response from Paperclip API."""
    return {
        "id": "issue-uuid-1",
        "identifier": "NFM-439",
        "title": "Implement feature X",
        "status": "in_review",
        "description": "## Objective\nDo the thing.\n\n## Acceptance Criteria\n- [ ] Criterion 1",
        "assigneeAgentId": "agent-uuid-1",
        "assigneeAgent": {
            "id": "agent-uuid-1",
            "name": "Lead Engineer",
            "urlKey": "lead-engineer",
            "role": "lead_engineer",
        },
        "parentIssueId": None,
        "comments": {"totalCount": 5},
    }


@pytest.fixture
def sample_comments() -> list[dict]:
    """Comments with code review approval."""
    return [
        {
            "id": "c1",
            "authorAgentId": "code-reviewer-uuid",
            "authorAgent": {"name": "Code Reviewer", "urlKey": "code-reviewer"},
            "body": "LGTM. APPROVED.",
            "createdAt": "2026-06-25T10:00:00Z",
        },
        {
            "id": "c2",
            "authorAgentId": "cpo-uuid",
            "authorAgent": {"name": "CPO", "urlKey": "cpo"},
            "body": "Accept. Looks good.",
            "createdAt": "2026-06-25T10:30:00Z",
        },
    ]


@pytest.fixture
def child_issues() -> list[dict]:
    """Child issues all in done status."""
    return [
        {"id": "child-1", "identifier": "NFM-439.1", "status": "done"},
        {"id": "child-2", "identifier": "NFM-439.2", "status": "done"},
    ]


# ---------------------------------------------------------------------------
# CLI Argument Parsing
# ---------------------------------------------------------------------------


class TestParseArgs:
    def test_parses_issue_id_and_role(self) -> None:
        args = parse_args(["--issue-id", "NFM-439", "--role", "cto"])
        assert args.issue_id == "NFM-439"
        assert args.role == "cto"

    def test_defaults_role_to_auto(self) -> None:
        args = parse_args(["--issue-id", "NFM-439"])
        assert args.role == "auto"

    def test_rejects_invalid_role(self) -> None:
        with pytest.raises(SystemExit):
            parse_args(["--issue-id", "NFM-439", "--role", "invalid_role"])


# ---------------------------------------------------------------------------
# Role Detection
# ---------------------------------------------------------------------------


class TestDetectRole:
    def test_detects_lead_engineer(self, sample_issue: dict) -> None:
        assert detect_role(sample_issue) == "lead_engineer"

    def test_detects_cto(self) -> None:
        issue = {
            "assigneeAgent": {"role": "cto", "urlKey": "cto"},
        }
        assert detect_role(issue) == "cto"

    def test_detects_cpo(self) -> None:
        issue = {
            "assigneeAgent": {"role": "cpo", "urlKey": "cpo"},
        }
        assert detect_role(issue) == "cpo"

    def test_falls_back_to_url_key(self) -> None:
        issue = {
            "assigneeAgent": {"role": "", "urlKey": "lead-engineer"},
        }
        assert detect_role(issue) == "lead_engineer"

    def test_returns_unknown_for_no_assignee(self) -> None:
        issue = {"assigneeAgent": None}
        assert detect_role(issue) == "unknown"


# ---------------------------------------------------------------------------
# Individual Check Functions
# ---------------------------------------------------------------------------


class TestCheckCodeReviewApproved:
    def test_passes_with_approved_comment(self, sample_comments: list[dict]) -> None:
        result = check_code_review_approved(sample_comments)
        assert result.passed is True

    def test_fails_without_approved_comment(self) -> None:
        comments = [
            {"id": "c1", "authorAgent": {"name": "dev"}, "body": "Please fix typo."},
        ]
        result = check_code_review_approved(comments)
        assert result.passed is False

    def test_passes_with_case_insensitive_approve(self) -> None:
        comments = [
            {"id": "c1", "authorAgent": {"name": "Code Reviewer"}, "body": "approved"},
        ]
        result = check_code_review_approved(comments)
        assert result.passed is True


class TestCheckTestsPassing:
    def test_passes_with_test_evidence(self, sample_comments: list[dict]) -> None:
        comments_with_tests = sample_comments + [
            {
                "id": "c3",
                "authorAgent": {"name": "Lead Engineer"},
                "body": "All 42 tests passing. CI green.",
            },
        ]
        result = check_tests_passing(comments_with_tests)
        assert result.passed is True

    def test_fails_without_test_evidence(self, sample_comments: list[dict]) -> None:
        result = check_tests_passing(sample_comments)
        assert result.passed is False

    def test_detects_pytest_failure(self) -> None:
        comments = [
            {"id": "c1", "body": "pytest: 3 failed, 12 passed"},
        ]
        result = check_tests_passing(comments)
        assert result.passed is False


class TestCheckCoverageGe80:
    def test_passes_with_high_coverage(self, sample_comments: list[dict]) -> None:
        comments = sample_comments + [
            {"id": "c3", "body": "Coverage: 92%. All green."},
        ]
        result = check_coverage_ge_80(comments)
        assert result.passed is True

    def test_fails_with_low_coverage(self, sample_comments: list[dict]) -> None:
        comments = sample_comments + [
            {"id": "c3", "body": "Coverage: 65%. Need more tests."},
        ]
        result = check_coverage_ge_80(comments)
        assert result.passed is False
        assert "65%" in (result.reason or "")

    def test_fails_without_coverage_mention(self) -> None:
        result = check_coverage_ge_80([])
        assert result.passed is False


class TestCheckCiGreen:
    def test_passes_with_ci_green_mention(self, sample_comments: list[dict]) -> None:
        comments = sample_comments + [
            {"id": "c3", "body": "CI: all green ✅"},
        ]
        result = check_ci_green(comments)
        assert result.passed is True

    def test_fails_without_ci_mention(self) -> None:
        result = check_ci_green([])
        assert result.passed is False


class TestCheckTechDocsUpdated:
    def test_passes_with_doc_update_mention(self) -> None:
        comments = [
            {"id": "c1", "body": "Updated DEVELOPMENT.md and API docs."},
        ]
        result = check_tech_docs_updated(comments)
        assert result.passed is True

    def test_fails_without_doc_update(self) -> None:
        result = check_tech_docs_updated([])
        assert result.passed is False


class TestCheckStagingVerified:
    def test_passes_with_staging_mention(self) -> None:
        comments = [
            {"id": "c1", "body": "Verified on staging environment. Works correctly."},
        ]
        result = check_staging_verified(comments)
        assert result.passed is True

    def test_fails_without_staging_mention(self) -> None:
        result = check_staging_verified([])
        assert result.passed is False


class TestCheckChildrenComplete:
    def test_passes_when_all_children_done(self, child_issues: list[dict]) -> None:
        result = check_children_complete(child_issues)
        assert result.passed is True

    def test_passes_when_no_children_exist(self) -> None:
        result = check_children_complete([])
        assert result.passed is True

    def test_fails_when_child_in_progress(self) -> None:
        children = [
            {"id": "c1", "status": "done"},
            {"id": "c2", "status": "in_progress"},
        ]
        result = check_children_complete(children)
        assert result.passed is False
        assert result.reason is not None

    def test_passes_when_children_done_or_cancelled(self) -> None:
        children = [
            {"id": "c1", "status": "done"},
            {"id": "c2", "status": "cancelled"},
        ]
        result = check_children_complete(children)
        assert result.passed is True


class TestCheckCpoAccepted:
    def test_passes_with_cpo_accept_comment(self, sample_comments: list[dict]) -> None:
        result = check_cpo_accepted(sample_comments)
        assert result.passed is True

    def test_fails_without_cpo_accept(self) -> None:
        comments = [
            {"id": "c1", "authorAgent": {"name": "Dev"}, "body": "Implementation done."},
        ]
        result = check_cpo_accepted(comments)
        assert result.passed is False

    def test_fails_with_empty_comments(self) -> None:
        result = check_cpo_accepted([])
        assert result.passed is False


class TestCheckAcceptanceCriteriaDefined:
    def test_passes_with_ac_section(self, sample_issue: dict) -> None:
        result = check_acceptance_criteria_defined(sample_issue)
        assert result.passed is True

    def test_fails_without_ac_section(self) -> None:
        issue = {
            "description": "## Objective\nDo the thing.\n\nNo AC here.",
        }
        result = check_acceptance_criteria_defined(issue)
        assert result.passed is False

    def test_passes_with_checklist_in_ac(self) -> None:
        issue = {
            "description": "## Acceptance Criteria\n- [x] Done\n- [ ] TODO",
        }
        result = check_acceptance_criteria_defined(issue)
        assert result.passed is True


# ---------------------------------------------------------------------------
# ROLE_CHECKS Registry
# ---------------------------------------------------------------------------


class TestRoleChecksRegistry:
    def test_cto_has_four_checks(self) -> None:
        assert len(ROLE_CHECKS["cto"]) == 4

    def test_cpo_has_three_checks(self) -> None:
        assert len(ROLE_CHECKS["cpo"]) == 3

    def test_lead_engineer_has_four_checks(self) -> None:
        assert len(ROLE_CHECKS["lead_engineer"]) == 4

    def test_all_check_names_are_strings(self) -> None:
        for role_checks in ROLE_CHECKS.values():
            for name, _ in role_checks:
                assert isinstance(name, str)
                assert len(name) > 0


# ---------------------------------------------------------------------------
# API Fetch Functions (Mocked)
# ---------------------------------------------------------------------------


class TestFetchIssue:
    def test_fetches_issue_by_identifier(
        self, base_url: str, api_key: str, sample_issue: dict
    ) -> None:
        with patch("scripts.okr.done_compliance._api_get") as mock_api_get:
            mock_api_get.return_value = [sample_issue]

            result = fetch_issue(base_url, api_key, "NFM-439")
            assert result is not None
            assert result["identifier"] == "NFM-439"

    def test_returns_none_on_api_error(self, base_url: str, api_key: str) -> None:
        with patch("scripts.okr.done_compliance._api_get") as mock_api_get:
            mock_api_get.return_value = None
            result = fetch_issue(base_url, api_key, "NFM-999")
            assert result is None


class TestFetchIssueComments:
    def test_fetches_comments(
        self, base_url: str, api_key: str, sample_comments: list[dict]
    ) -> None:
        with patch("scripts.okr.done_compliance._api_get") as mock_api_get:
            mock_api_get.return_value = sample_comments
            result = fetch_issue_comments(base_url, api_key, "issue-uuid-1")
            assert len(result) == 2
            assert result[0]["id"] == "c1"

    def test_returns_empty_list_on_error(self, base_url: str, api_key: str) -> None:
        with patch("scripts.okr.done_compliance._api_get") as mock_api_get:
            mock_api_get.return_value = None
            result = fetch_issue_comments(base_url, api_key, "issue-uuid-1")
            assert result == []


class TestFetchChildIssues:
    def test_fetches_children(
        self, base_url: str, api_key: str, child_issues: list[dict]
    ) -> None:
        with patch("scripts.okr.done_compliance._api_get") as mock_api_get:
            mock_api_get.return_value = child_issues
            result = fetch_child_issues(base_url, api_key, "company-1", "issue-uuid-1")
            assert len(result) == 2

    def test_returns_empty_list_on_error(self, base_url: str, api_key: str) -> None:
        with patch("scripts.okr.done_compliance._api_get") as mock_api_get:
            mock_api_get.return_value = None
            result = fetch_child_issues(
                base_url, api_key, "company-1", "issue-uuid-1"
            )
            assert result == []


# ---------------------------------------------------------------------------
# ComplianceReport Model
# ---------------------------------------------------------------------------


class TestComplianceReport:
    def test_json_output_matches_schema(self) -> None:
        checks = [
            ComplianceCheck(name="code_review_approved", passed=True),
            ComplianceCheck(name="tests_passing", passed=False, reason="No tests"),
        ]
        report = ComplianceReport(
            issue_id="NFM-439",
            role="lead_engineer",
            compliant=False,
            checked_at="2026-06-25T12:00:00Z",
            checks=checks,
        )
        output = json.loads(report.to_json())
        assert output["issueId"] == "NFM-439"
        assert output["role"] == "lead_engineer"
        assert output["compliant"] is False
        assert len(output["checks"]) == 2
        assert output["checks"][0]["name"] == "code_review_approved"
        assert output["checks"][0]["passed"] is True
        assert output["checks"][1]["reason"] == "No tests"

    def test_missing_items_excludes_passed_checks(self) -> None:
        checks = [
            ComplianceCheck(name="a", passed=True),
            ComplianceCheck(name="b", passed=False),
            ComplianceCheck(name="c", passed=True),
        ]
        report = ComplianceReport(
            issue_id="NFM-1",
            role="cto",
            compliant=False,
            checked_at="2026-06-25T12:00:00Z",
            checks=checks,
        )
        output = json.loads(report.to_json())
        assert output["missingItems"] == ["b"]


# ---------------------------------------------------------------------------
# build_report Integration
# ---------------------------------------------------------------------------


class TestBuildReport:
    def test_builds_compliant_report_for_lead_engineer(
        self,
        base_url: str,
        api_key: str,
        sample_issue: dict,
        sample_comments: list[dict],
    ) -> None:
        issue_with_tests = {
            **sample_issue,
            "description": "## Objective\n\n## Acceptance Criteria\n- [x] Tests pass",
        }
        comments_with_all = sample_comments + [
            {"id": "c3", "body": "All 85 tests passing. Coverage: 90%. CI: green."},
        ]
        children: list[dict] = []

        with patch("scripts.okr.done_compliance._api_get") as mock_api_get:
            mock_api_get.side_effect = [[issue_with_tests], comments_with_all, children]

            report = build_report(
                base_url=base_url,
                api_key=api_key,
                issue_identifier="NFM-439",
                company_id="company-1",
                role="lead_engineer",
            )

            assert report is not None
            assert report.role == "lead_engineer"
            assert report.issue_id == "NFM-439"

    def test_builds_non_compliant_report(
        self,
        base_url: str,
        api_key: str,
        sample_issue: dict,
    ) -> None:
        minimal_comments: list[dict] = []
        children: list[dict] = []

        with patch("scripts.okr.done_compliance._api_get") as mock_api_get:
            mock_api_get.side_effect = [[sample_issue], minimal_comments, children]

            report = build_report(
                base_url=base_url,
                api_key=api_key,
                issue_identifier="NFM-439",
                company_id="company-1",
                role="lead_engineer",
            )

            assert report is not None
            assert report.compliant is False
            assert len(report.missing_items) > 0
