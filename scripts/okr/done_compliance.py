#!/usr/bin/env python3
"""Done standard compliance check tool for Paperclip issues.

Checks whether an issue meets role-specific completion criteria before
being marked as ``done``. Read-only: never modifies issue status.

Usage:
    python scripts/okr/done_compliance.py --issue-id NFM-439 --role lead_engineer
    python scripts/okr/done_compliance.py --issue-id NFM-439  # auto-detect role

Exit code: 0 = compliant, 1 = non-compliant or error.

Environment variables:
    PAPERCLIP_API_URL  — Paperclip server base URL
    PAPERCLIP_API_KEY  — API key for authentication
    PAPERCLIP_COMPANY_ID — Company ID for multi-tenant queries
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ComplianceCheck:
    """Result of a single compliance check."""

    name: str
    passed: bool
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"name": self.name, "passed": self.passed}
        if not self.passed and self.reason:
            result["reason"] = self.reason
        return result


@dataclass
class ComplianceReport:
    """Aggregated compliance check results for an issue."""

    issue_id: str
    role: str
    compliant: bool
    checked_at: str
    checks: list[ComplianceCheck] = field(default_factory=list)

    @property
    def missing_items(self) -> list[str]:
        return [c.name for c in self.checks if not c.passed]

    def to_json(self) -> str:
        output = {
            "issueId": self.issue_id,
            "role": self.role,
            "compliant": self.compliant,
            "checkedAt": self.checked_at,
            "checks": [c.to_dict() for c in self.checks],
            "missingItems": self.missing_items,
        }
        return json.dumps(output, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Role -> Check Mapping
# ---------------------------------------------------------------------------

VALID_ROLES = {"cto", "cpo", "lead_engineer", "auto"}


# ---------------------------------------------------------------------------
# Role Detection
# ---------------------------------------------------------------------------


def detect_role(issue: dict) -> str:
    """Detect the assignee's role from issue data.

    Falls back to parsing the agent urlKey if role field is empty.
    Returns 'unknown' if no assignee information is available.
    """
    agent = issue.get("assigneeAgent")
    if not agent:
        return "unknown"

    role = agent.get("role", "")
    if role and role in VALID_ROLES:
        return role

    url_key = agent.get("urlKey", "")
    if url_key:
        normalized = url_key.replace("-", "_")
        if normalized in VALID_ROLES:
            return normalized

    return "unknown"


# ---------------------------------------------------------------------------
# Check Functions
# ---------------------------------------------------------------------------


def _any_comment_matches(
    comments: list[dict], pattern: str
) -> tuple[bool, str | None]:
    """Check if any comment body matches the given regex pattern."""
    regex = re.compile(pattern, re.IGNORECASE)
    for comment in comments:
        body = comment.get("body", "")
        if regex.search(body):
            return True, None
    return False, None


def check_code_review_approved(comments: list[dict]) -> ComplianceCheck:
    """Check for code review approval in comments."""
    passed, _ = _any_comment_matches(comments, r"\bapprov\w*\b")
    return ComplianceCheck(
        name="code_review_approved",
        passed=passed,
        reason="No code review approval found" if not passed else None,
    )


def check_tests_passing(comments: list[dict]) -> ComplianceCheck:
    """Check for evidence that tests are passing."""
    passed_pos, _ = _any_comment_matches(comments, r"\b\d+\s*tests?\s*(pass\w*|green)\b")
    passed_all, _ = _any_comment_matches(comments, r"\ball\s+tests?\s+pass\w*\b")
    passed = passed_pos or passed_all
    return ComplianceCheck(
        name="tests_passing",
        passed=passed,
        reason="No test results found" if not passed else None,
    )


def check_coverage_ge_80(comments: list[dict]) -> ComplianceCheck:
    """Check for test coverage >= 80% mentioned in comments."""
    coverage_pattern = re.compile(r"coverage[:\s]+(\d+)%", re.IGNORECASE)
    for comment in comments:
        body = comment.get("body", "")
        match = coverage_pattern.search(body)
        if match:
            pct = int(match.group(1))
            if pct >= 80:
                return ComplianceCheck(
                    name="coverage_ge_80",
                    passed=True,
                )
            return ComplianceCheck(
                name="coverage_ge_80",
                passed=False,
                reason=f"Coverage: {pct}%",
            )
    return ComplianceCheck(
        name="coverage_ge_80",
        passed=False,
        reason="No coverage data found",
    )


def check_ci_green(comments: list[dict]) -> ComplianceCheck:
    """Check for CI pipeline green status."""
    patterns = [
        r"\bci[:\s]*(all\s+)?green\b",
        r"\bci[:\s]*(pass\w*|✅)\b",
        r"\bgreen\s*(ci|pipeline|build)\b",
        r"\bci\s+green\b",
    ]
    for pattern in patterns:
        passed, _ = _any_comment_matches(comments, pattern)
        if passed:
            return ComplianceCheck(name="ci_green", passed=True)
    return ComplianceCheck(
        name="ci_green",
        passed=False,
        reason="No CI status found",
    )


def check_tech_docs_updated(comments: list[dict]) -> ComplianceCheck:
    """Check for evidence of technical documentation updates."""
    passed, _ = _any_comment_matches(
        comments,
        r"\b(updat\w*|wrot\w*|add\w*|modif\w*)\b.*\b(doc\w*|documentation|README|CHANGELOG|\.md)\b",
    )
    if not passed:
        passed, _ = _any_comment_matches(
            comments,
            r"\b(doc\w*|documentation|README|CHANGELOG|\.md)\b.*\b(updat\w*|wrot\w*|add\w*|modif\w*)\b",
        )
    return ComplianceCheck(
        name="tech_docs_updated",
        passed=passed,
        reason="No documentation update found" if not passed else None,
    )


def check_staging_verified(comments: list[dict]) -> ComplianceCheck:
    """Check for staging environment verification."""
    passed, _ = _any_comment_matches(
        comments, r"\bstag\w*\b.*\b(verif\w*|test\w*|confirm\w*|check\w*|work\w*)\b"
    )
    return ComplianceCheck(
        name="staging_verified",
        passed=passed,
        reason="No staging verification found" if not passed else None,
    )


def check_children_complete(child_issues: list[dict]) -> ComplianceCheck:
    """Check that all child issues are done or cancelled."""
    if not child_issues:
        return ComplianceCheck(
            name="children_complete",
            passed=True,
        )

    incomplete = [
        c.get("identifier", c.get("id", "unknown"))
        for c in child_issues
        if c.get("status") not in ("done", "cancelled")
    ]
    if incomplete:
        return ComplianceCheck(
            name="children_complete",
            passed=False,
            reason=f"Incomplete children: {', '.join(incomplete)}",
        )
    return ComplianceCheck(
        name="children_complete",
        passed=True,
    )


def check_cpo_accepted(comments: list[dict]) -> ComplianceCheck:
    """Check for CPO acceptance comment."""
    accept_pattern = re.compile(r"\b(accept\w*|approved|looks?\s+good|LGTM)\b", re.IGNORECASE)
    for comment in comments:
        body = comment.get("body", "")
        author = comment.get("authorAgent", {})
        author_name = author.get("name", "").lower() if author else ""
        author_key = author.get("urlKey", "").lower() if author else ""
        if ("cpo" in author_name or "cpo" in author_key) and accept_pattern.search(body):
            return ComplianceCheck(
                name="cpo_accepted",
                passed=True,
            )
    return ComplianceCheck(
        name="cpo_accepted",
        passed=False,
        reason="No CPO acceptance found",
    )


def check_acceptance_criteria_defined(issue: dict) -> ComplianceCheck:
    """Check that the issue description contains an AC section."""
    description = issue.get("description", "")
    ac_pattern = re.compile(
        r"##\s*acceptance\s+criteria|##\s*ac\b",
        re.IGNORECASE,
    )
    passed = bool(ac_pattern.search(description))
    return ComplianceCheck(
        name="acceptance_criteria_defined",
        passed=passed,
        reason="No Acceptance Criteria section found" if not passed else None,
    )


# ---------------------------------------------------------------------------
# Role -> Check Mapping
# ---------------------------------------------------------------------------


def _get_role_checks(role: str) -> list[tuple[str, Any]]:
    """Return the check functions for a given role.

    Each entry is (check_name, check_function).
    """
    return ROLE_CHECKS.get(role, [])


ROLE_CHECKS: dict[str, list[tuple[str, Any]]] = {
    "cto": [
        ("code_review_approved", check_code_review_approved),
        ("ci_green", check_ci_green),
        ("tech_docs_updated", check_tech_docs_updated),
        ("staging_verified", check_staging_verified),
    ],
    "cpo": [
        ("children_complete", None),  # uses child_issues, not comments
        ("acceptance_criteria_defined", None),  # uses issue description
        ("cpo_accepted", check_cpo_accepted),
    ],
    "lead_engineer": [
        ("code_review_approved", check_code_review_approved),
        ("tests_passing", check_tests_passing),
        ("coverage_ge_80", check_coverage_ge_80),
        ("ci_green", check_ci_green),
    ],
}


# ---------------------------------------------------------------------------
# Paperclip API Client
# ---------------------------------------------------------------------------


def _api_get(base_url: str, api_key: str, path: str) -> dict | list | None:
    """Make an authenticated GET request to the Paperclip API."""
    url = f"{base_url}{path}"
    req = Request(url, method="GET")
    req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", "DoneComplianceCheck/1.0")

    try:
        with urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data
    except (HTTPError, OSError, json.JSONDecodeError, Exception):
        return None


def fetch_issue(
    base_url: str, api_key: str, issue_identifier: str
) -> dict | None:
    """Fetch an issue by its identifier (e.g. NFM-439)."""
    company_id = os.environ.get("PAPERCLIP_COMPANY_ID", "")
    if company_id:
        path = f"/api/companies/{company_id}/issues?identifier={issue_identifier}"
        result = _api_get(base_url, api_key, path)
        if isinstance(result, list) and len(result) > 0:
            return result[0]
        return None
    return None


def fetch_issue_comments(
    base_url: str, api_key: str, issue_id: str
) -> list[dict]:
    """Fetch all comments for an issue."""
    result = _api_get(base_url, api_key, f"/api/issues/{issue_id}/comments")
    if isinstance(result, list):
        return result
    return []


def fetch_child_issues(
    base_url: str, api_key: str, company_id: str, issue_id: str
) -> list[dict]:
    """Fetch child issues of a given issue."""
    result = _api_get(
        base_url,
        api_key,
        f"/api/companies/{company_id}/issues?parentId={issue_id}",
    )
    if isinstance(result, list):
        return result
    return []


# ---------------------------------------------------------------------------
# Report Builder
# ---------------------------------------------------------------------------


def build_report(
    base_url: str,
    api_key: str,
    issue_identifier: str,
    company_id: str,
    role: str = "auto",
) -> ComplianceReport | None:
    """Build a compliance report for the given issue and role.

    Returns None if the issue cannot be fetched.
    """
    issue = fetch_issue(base_url, api_key, issue_identifier)
    if not issue:
        return None

    if role == "auto":
        role = detect_role(issue)

    if role == "unknown":
        return ComplianceReport(
            issue_id=issue_identifier,
            role="unknown",
            compliant=False,
            checked_at=datetime.now(timezone.utc).isoformat(),
            checks=[],
        )

    comments = fetch_issue_comments(base_url, api_key, issue.get("id", ""))
    child_issues = fetch_child_issues(
        base_url, api_key, company_id, issue.get("id", "")
    )

    checks: list[ComplianceCheck] = []
    check_entries = _get_role_checks(role)

    for check_name, check_fn in check_entries:
        if check_name == "children_complete":
            checks.append(check_children_complete(child_issues))
        elif check_name == "acceptance_criteria_defined":
            checks.append(check_acceptance_criteria_defined(issue))
        elif check_fn is not None:
            checks.append(check_fn(comments))
        else:
            checks.append(
                ComplianceCheck(name=check_name, passed=False, reason="No checker")
            )

    all_passed = all(c.passed for c in checks)

    return ComplianceReport(
        issue_id=issue_identifier,
        role=role,
        compliant=all_passed,
        checked_at=datetime.now(timezone.utc).isoformat(),
        checks=checks,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Check Done standard compliance for a Paperclip issue.",
    )
    parser.add_argument(
        "--issue-id",
        required=True,
        help="Issue identifier (e.g. NFM-439)",
    )
    parser.add_argument(
        "--role",
        choices=list(VALID_ROLES),
        default="auto",
        help="Role to check (default: auto-detect from assignee)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Entry point: run compliance check and output JSON result."""
    args = parse_args(argv)

    base_url = os.environ.get("PAPERCLIP_API_URL", "")
    api_key = os.environ.get("PAPERCLIP_API_KEY", "")
    company_id = os.environ.get("PAPERCLIP_COMPANY_ID", "")

    if not base_url:
        print("Error: PAPERCLIP_API_URL not set", file=sys.stderr)
        return 1
    if not api_key:
        print("Error: PAPERCLIP_API_KEY not set", file=sys.stderr)
        return 1
    if not company_id:
        print("Error: PAPERCLIP_COMPANY_ID not set", file=sys.stderr)
        return 1

    report = build_report(
        base_url=base_url,
        api_key=api_key,
        issue_identifier=args.issue_id,
        company_id=company_id,
        role=args.role,
    )

    if report is None:
        print(f"Error: Issue {args.issue_id} not found", file=sys.stderr)
        return 1

    print(report.to_json())
    return 0 if report.compliant else 1


if __name__ == "__main__":
    sys.exit(main())
