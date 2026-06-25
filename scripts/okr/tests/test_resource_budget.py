"""Unit tests for resource_budget.py — Sprint resource budget enforcement.

Tests cover CLI argument parsing, Paperclip API interaction, budget
calculation, JSON report formatting, and alert issue creation.
"""

from __future__ import annotations

import json
import urllib.error
from datetime import date
from unittest import mock
from urllib.request import Request

import pytest

from scripts.okr.resource_budget import (
    AgentBudget,
    BudgetConfig,
    BudgetReport,
    build_alert_body,
    build_alert_title,
    check_budget,
    count_created_this_sprint,
    count_in_progress,
    create_budget_alert,
    fetch_agent_issues,
    fetch_all_agents,
    format_report,
    main,
    parse_args,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def sample_config() -> BudgetConfig:
    """Standard budget configuration for testing."""
    return BudgetConfig(
        creation_budget=5,
        wip_budget=3,
        sprint_start=date(2026, 6, 23),
        sprint_end=date(2026, 7, 6),
    )


@pytest.fixture()
def sample_issues() -> list[dict]:
    """Sample issue data mimicking Paperclip API response."""
    return [
        {
            "id": "issue-1",
            "identifier": "NFM-440",
            "title": "Some task",
            "status": "in_progress",
            "assigneeAgentId": "agent-1",
            "assigneeAgentName": "Lead Engineer",
            "createdAt": "2026-06-24T10:00:00Z",
        },
        {
            "id": "issue-2",
            "identifier": "NFM-439",
            "title": "Another task",
            "status": "in_progress",
            "assigneeAgentId": "agent-1",
            "assigneeAgentName": "Lead Engineer",
            "createdAt": "2026-06-25T08:00:00Z",
        },
        {
            "id": "issue-3",
            "identifier": "NFM-438",
            "title": "Old task",
            "status": "done",
            "assigneeAgentId": "agent-1",
            "assigneeAgentName": "Lead Engineer",
            "createdAt": "2026-06-20T12:00:00Z",
        },
        {
            "id": "issue-4",
            "identifier": "NFM-441",
            "title": "New task",
            "status": "todo",
            "assigneeAgentId": "agent-1",
            "assigneeAgentName": "Lead Engineer",
            "createdAt": "2026-06-24T14:00:00Z",
        },
        {
            "id": "issue-5",
            "identifier": "NFM-442",
            "title": "CTO review",
            "status": "in_progress",
            "assigneeAgentId": "agent-2",
            "assigneeAgentName": "CTO",
            "createdAt": "2026-06-23T09:00:00Z",
        },
    ]


# ---------------------------------------------------------------------------
# parse_args
# ---------------------------------------------------------------------------


class TestParseArgs:
    """Tests for CLI argument parsing."""

    def test_required_args(self) -> None:
        result = parse_args(["check", "--sprint-start", "2026-06-23", "--sprint-end", "2026-07-06"])
        assert result.command == "check"
        assert result.sprint_start == date(2026, 6, 23)
        assert result.sprint_end == date(2026, 7, 6)
        assert result.creation_budget == 5
        assert result.wip_budget == 3
        assert not result.create_alerts

    def test_custom_budgets(self) -> None:
        result = parse_args([
            "check",
            "--sprint-start", "2026-06-23",
            "--sprint-end", "2026-07-06",
            "--creation-budget", "10",
            "--wip-budget", "5",
        ])
        assert result.creation_budget == 10
        assert result.wip_budget == 5

    def test_create_alerts_flag(self) -> None:
        result = parse_args([
            "check",
            "--sprint-start", "2026-06-23",
            "--sprint-end", "2026-07-06",
            "--create-alerts",
        ])
        assert result.create_alerts is True

    def test_missing_sprint_start_raises(self) -> None:
        with pytest.raises(SystemExit):
            parse_args(["check", "--sprint-end", "2026-07-06"])

    def test_missing_sprint_end_raises(self) -> None:
        with pytest.raises(SystemExit):
            parse_args(["check", "--sprint-start", "2026-06-23"])


# ---------------------------------------------------------------------------
# count_created_this_sprint
# ---------------------------------------------------------------------------


class TestCountCreatedThisSprint:
    """Tests for counting issues created within sprint date range."""

    def test_counts_issues_within_range(
        self, sample_issues: list[dict], sample_config: BudgetConfig
    ) -> None:
        count = count_created_this_sprint(sample_issues, sample_config)
        # issue-1 (06-24), issue-2 (06-25), issue-4 (06-24), issue-5 (06-23)
        # all within [2026-06-23, 2026-07-06]; issue-3 (06-20) is before sprint
        assert count == 4

    def test_excludes_issues_before_sprint(
        self, sample_issues: list[dict]
    ) -> None:
        config = BudgetConfig(
            creation_budget=5,
            wip_budget=3,
            sprint_start=date(2026, 6, 25),
            sprint_end=date(2026, 7, 6),
        )
        count = count_created_this_sprint(sample_issues, config)
        # Only issue-2 (2026-06-25) counts — others are before
        assert count == 1

    def test_empty_issues_returns_zero(self, sample_config: BudgetConfig) -> None:
        assert count_created_this_sprint([], sample_config) == 0

    def test_treats_start_date_as_inclusive(
        self, sample_config: BudgetConfig
    ) -> None:
        issue = [{
            "id": "x",
            "createdAt": "2026-06-23T00:00:00Z",
        }]
        count = count_created_this_sprint(issue, sample_config)
        assert count == 1


# ---------------------------------------------------------------------------
# count_in_progress
# ---------------------------------------------------------------------------


class TestCountInProgress:
    """Tests for counting currently in_progress issues."""

    def test_counts_in_progress_issues(self, sample_issues: list[dict]) -> None:
        count = count_in_progress(sample_issues)
        assert count == 3  # issue-1, issue-2, issue-5

    def test_excludes_done_issues(self, sample_issues: list[dict]) -> None:
        only_done = [i for i in sample_issues if i["status"] == "done"]
        assert count_in_progress(only_done) == 0

    def test_empty_issues_returns_zero(self) -> None:
        assert count_in_progress([]) == 0


# ---------------------------------------------------------------------------
# fetch_agent_issues
# ---------------------------------------------------------------------------


class TestFetchAgentIssues:
    """Tests for fetching issues from Paperclip API."""

    def test_returns_issues_on_success(
        self, sample_issues: list[dict]
    ) -> None:
        api_response = {"issues": sample_issues}
        mock_open = mock.MagicMock()
        mock_open.read.return_value = json.dumps(api_response).encode()
        mock_open.__enter__ = mock.Mock(return_value=mock_open)
        mock_open.__exit__ = mock.Mock(return_value=False)

        with mock.patch("urllib.request.urlopen", return_value=mock_open):
            result = fetch_agent_issues(
                api_url="http://localhost:3100",
                company_id="co-1",
                agent_id="agent-1",
                api_key="test-key",
            )
        assert len(result) == 5
        assert result[0]["id"] == "issue-1"

    def test_returns_empty_on_api_error(self) -> None:
        with mock.patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("connection refused"),
        ):
            result = fetch_agent_issues(
                api_url="http://localhost:3100",
                company_id="co-1",
                agent_id="agent-1",
                api_key="test-key",
            )
        assert result == []

    def test_sends_correct_headers(self) -> None:
        api_response = {"issues": []}
        mock_open = mock.MagicMock()
        mock_open.read.return_value = json.dumps(api_response).encode()
        mock_open.__enter__ = mock.Mock(return_value=mock_open)
        mock_open.__exit__ = mock.Mock(return_value=False)

        with mock.patch("urllib.request.urlopen", return_value=mock_open) as mock_urlopen:
            fetch_agent_issues(
                api_url="http://localhost:3100",
                company_id="co-1",
                agent_id="agent-1",
                api_key="test-key",
            )

        call_args = mock_urlopen.call_args
        req: Request = call_args[0][0]
        assert req.get_header("Authorization") == "Bearer test-key"


# ---------------------------------------------------------------------------
# fetch_all_agents
# ---------------------------------------------------------------------------


class TestFetchAllAgents:
    """Tests for fetching agent list from Paperclip API."""

    def test_returns_agent_list(self) -> None:
        api_response = {
            "agents": [
                {"id": "agent-1", "name": "Lead Engineer", "role": "lead_engineer"},
                {"id": "agent-2", "name": "CTO", "role": "cto"},
            ]
        }
        mock_open = mock.MagicMock()
        mock_open.read.return_value = json.dumps(api_response).encode()
        mock_open.__enter__ = mock.Mock(return_value=mock_open)
        mock_open.__exit__ = mock.Mock(return_value=False)

        with mock.patch("urllib.request.urlopen", return_value=mock_open):
            result = fetch_all_agents(
                api_url="http://localhost:3100",
                company_id="co-1",
                api_key="test-key",
            )
        assert len(result) == 2
        assert result[0]["id"] == "agent-1"

    def test_returns_empty_on_error(self) -> None:
        with mock.patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("fail"),
        ):
            result = fetch_all_agents(
                api_url="http://localhost:3100",
                company_id="co-1",
                api_key="test-key",
            )
        assert result == []


# ---------------------------------------------------------------------------
# check_budget
# ---------------------------------------------------------------------------


class TestCheckBudget:
    """Tests for computing per-agent budget status."""

    def test_agent_within_budget(
        self, sample_issues: list[dict], sample_config: BudgetConfig
    ) -> None:
        agent_issues = [i for i in sample_issues if i["assigneeAgentId"] == "agent-1"]
        budget = check_budget(agent_issues, "agent-1", "Lead Engineer", sample_config)

        assert budget.agent_id == "agent-1"
        assert budget.agent_name == "Lead Engineer"
        assert budget.created_this_sprint == 3
        assert budget.creation_budget == 5
        assert abs(budget.creation_used - 0.6) < 0.01
        assert budget.in_progress == 2
        assert budget.wip_budget == 3
        assert abs(budget.wip_used - 0.667) < 0.01
        assert budget.over_budget is False

    def test_agent_over_creation_budget(self, sample_config: BudgetConfig) -> None:
        issues = [
            {"id": f"i-{i}", "status": "todo", "createdAt": "2026-06-24T00:00:00Z"}
            for i in range(7)
        ]
        budget = check_budget(issues, "agent-x", "Busy Agent", sample_config)
        assert budget.created_this_sprint == 7
        assert budget.over_budget is True

    def test_agent_over_wip_budget(self, sample_config: BudgetConfig) -> None:
        issues = [
            {"id": f"i-{i}", "status": "in_progress", "createdAt": "2026-06-15T00:00:00Z"}
            for i in range(4)
        ]
        budget = check_budget(issues, "agent-y", "Overloaded Agent", sample_config)
        assert budget.in_progress == 4
        assert budget.over_budget is True


# ---------------------------------------------------------------------------
# format_report
# ---------------------------------------------------------------------------


class TestFormatReport:
    """Tests for JSON report formatting."""

    def test_produces_valid_json(self) -> None:
        budgets = [
            AgentBudget(
                agent_id="agent-1",
                agent_name="Lead Engineer",
                created_this_sprint=3,
                creation_budget=5,
                creation_used=0.6,
                in_progress=2,
                wip_budget=3,
                wip_used=0.667,
                over_budget=False,
            ),
            AgentBudget(
                agent_id="agent-2",
                agent_name="Busy Agent",
                created_this_sprint=6,
                creation_budget=5,
                creation_used=1.2,
                in_progress=2,
                wip_budget=3,
                wip_used=0.667,
                over_budget=True,
            ),
        ]
        report = BudgetReport(
            sprint_start=date(2026, 6, 23),
            sprint_end=date(2026, 7, 6),
            agents=budgets,
        )
        output = format_report(report)
        parsed = json.loads(output)

        assert parsed["sprint"]["start"] == "2026-06-23"
        assert parsed["sprint"]["end"] == "2026-07-06"
        assert len(parsed["agents"]) == 2
        assert parsed["summary"]["totalAgents"] == 2
        assert parsed["summary"]["overBudgetAgents"] == 1

    def test_empty_agents(self) -> None:
        report = BudgetReport(
            sprint_start=date(2026, 6, 23),
            sprint_end=date(2026, 7, 6),
            agents=[],
        )
        output = format_report(report)
        parsed = json.loads(output)
        assert parsed["summary"]["totalAgents"] == 0
        assert parsed["summary"]["overBudgetAgents"] == 0


# ---------------------------------------------------------------------------
# build_alert_title / build_alert_body
# ---------------------------------------------------------------------------


class TestAlertContent:
    """Tests for budget alert issue content generation."""

    def test_alert_title_creation(self) -> None:
        budget = AgentBudget(
            agent_id="agent-1",
            agent_name="Lead Engineer",
            created_this_sprint=7,
            creation_budget=5,
            creation_used=1.4,
            in_progress=2,
            wip_budget=3,
            wip_used=0.667,
            over_budget=True,
        )
        title = build_alert_title(budget)
        assert "Lead Engineer" in title
        assert "BUDGET ALERT" in title

    def test_alert_body_contains_details(self) -> None:
        budget = AgentBudget(
            agent_id="agent-1",
            agent_name="Lead Engineer",
            created_this_sprint=7,
            creation_budget=5,
            creation_used=1.4,
            in_progress=2,
            wip_budget=3,
            wip_used=0.667,
            over_budget=True,
        )
        body = build_alert_body(
            budget,
            sprint_start=date(2026, 6, 23),
            sprint_end=date(2026, 7, 6),
        )
        assert "7" in body
        assert "5" in body
        assert "2026-06-23" in body
        assert "2026-07-06" in body


# ---------------------------------------------------------------------------
# create_budget_alert
# ---------------------------------------------------------------------------


class TestCreateBudgetAlert:
    """Tests for creating budget alert issues via Paperclip API."""

    def test_creates_alert_on_success(
        self, sample_config: BudgetConfig
    ) -> None:
        budget = AgentBudget(
            agent_id="agent-1",
            agent_name="Lead Engineer",
            created_this_sprint=7,
            creation_budget=5,
            creation_used=1.4,
            in_progress=2,
            wip_budget=3,
            wip_used=0.667,
            over_budget=True,
        )
        api_response = {
            "id": "new-issue-id",
            "identifier": "NFM-999",
            "title": build_alert_title(budget),
        }
        mock_open = mock.MagicMock()
        mock_open.read.return_value = json.dumps(api_response).encode()
        mock_open.__enter__ = mock.Mock(return_value=mock_open)
        mock_open.__exit__ = mock.Mock(return_value=False)

        with mock.patch("urllib.request.urlopen", return_value=mock_open):
            result = create_budget_alert(
                api_url="http://localhost:3100",
                company_id="co-1",
                api_key="test-key",
                budget=budget,
                config=sample_config,
                cto_agent_id="cto-id",
            )

        assert result is not None
        assert result["identifier"] == "NFM-999"

    def test_returns_none_on_api_error(
        self, sample_config: BudgetConfig
    ) -> None:
        budget = AgentBudget(
            agent_id="agent-1",
            agent_name="Lead Engineer",
            created_this_sprint=7,
            creation_budget=5,
            creation_used=1.4,
            in_progress=2,
            wip_budget=3,
            wip_used=0.667,
            over_budget=True,
        )
        with mock.patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("fail"),
        ):
            result = create_budget_alert(
                api_url="http://localhost:3100",
                company_id="co-1",
                api_key="test-key",
                budget=budget,
                config=sample_config,
                cto_agent_id="cto-id",
            )
        assert result is None

    def test_includes_parent_id_when_provided(
        self, sample_config: BudgetConfig
    ) -> None:
        budget = AgentBudget(
            agent_id="agent-1",
            agent_name="Lead Engineer",
            created_this_sprint=7,
            creation_budget=5,
            creation_used=1.4,
            in_progress=2,
            wip_budget=3,
            wip_used=0.667,
            over_budget=True,
        )
        api_response = {"id": "new-id", "identifier": "NFM-999"}
        mock_open = mock.MagicMock()
        mock_open.read.return_value = json.dumps(api_response).encode()
        mock_open.__enter__ = mock.Mock(return_value=mock_open)
        mock_open.__exit__ = mock.Mock(return_value=False)

        with mock.patch("urllib.request.urlopen", return_value=mock_open) as mock_urlopen:
            create_budget_alert(
                api_url="http://localhost:3100",
                company_id="co-1",
                api_key="test-key",
                budget=budget,
                config=sample_config,
                cto_agent_id="cto-id",
                parent_issue_id="parent-id",
            )

        # Verify the POST body includes parentId
        call_args = mock_urlopen.call_args
        posted_data = json.loads(call_args[0][0].data.decode())
        assert posted_data["parentId"] == "parent-id"


# ---------------------------------------------------------------------------
# _parse_created_at
# ---------------------------------------------------------------------------


class TestParseCreatedAt:
    """Tests for ISO-8601 createdAt date parsing."""

    def test_parse_standard_iso(self) -> None:
        from scripts.okr.resource_budget import _parse_created_at

        result = _parse_created_at("2026-06-24T10:00:00Z")
        assert result == date(2026, 6, 24)

    def test_parse_with_offset(self) -> None:
        from scripts.okr.resource_budget import _parse_created_at

        result = _parse_created_at("2026-06-24T10:00:00+08:00")
        assert result == date(2026, 6, 24)

    def test_parse_invalid_returns_none(self) -> None:
        from scripts.okr.resource_budget import _parse_created_at

        assert _parse_created_at("not-a-date") is None
        assert _parse_created_at("") is None

    def test_parse_missing_field(self) -> None:
        from scripts.okr.resource_budget import _parse_created_at

        assert _parse_created_at(None) is None


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


class TestMain:
    """Tests for the main entry point."""

    def test_missing_env_vars_returns_1(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            result = main([
                "check",
                "--sprint-start", "2026-06-23",
                "--sprint-end", "2026-07-06",
            ])
        assert result == 1

    def test_all_within_budget_returns_0(self) -> None:
        env = {
            "PAPERCLIP_API_URL": "http://localhost:3100",
            "PAPERCLIP_API_KEY": "test-key",
            "PAPERCLIP_COMPANY_ID": "co-1",
        }

        agents_resp = {"agents": [{"id": "a1", "name": "Agent1"}]}
        issues_resp = {"issues": [
            {"id": "i1", "status": "in_progress", "createdAt": "2026-06-24T10:00:00Z"},
        ]}

        mock_agents = mock.MagicMock()
        mock_agents.read.return_value = json.dumps(agents_resp).encode()
        mock_agents.__enter__ = mock.Mock(return_value=mock_agents)
        mock_agents.__exit__ = mock.Mock(return_value=False)

        mock_issues = mock.MagicMock()
        mock_issues.read.return_value = json.dumps(issues_resp).encode()
        mock_issues.__enter__ = mock.Mock(return_value=mock_issues)
        mock_issues.__exit__ = mock.Mock(return_value=False)

        with mock.patch.dict("os.environ", env, clear=True):
            with mock.patch("urllib.request.urlopen", side_effect=[mock_agents, mock_issues]):
                result = main([
                    "check",
                    "--sprint-start", "2026-06-23",
                    "--sprint-end", "2026-07-06",
                ])
        assert result == 0

    def test_over_budget_returns_2(self) -> None:
        env = {
            "PAPERCLIP_API_URL": "http://localhost:3100",
            "PAPERCLIP_API_KEY": "test-key",
            "PAPERCLIP_COMPANY_ID": "co-1",
        }

        agents_resp = {"agents": [{"id": "a1", "name": "Agent1"}]}
        issues_resp = {"issues": [
            {"id": f"i{i}", "status": "todo", "createdAt": "2026-06-24T10:00:00Z"}
            for i in range(6)
        ]}

        mock_agents = mock.MagicMock()
        mock_agents.read.return_value = json.dumps(agents_resp).encode()
        mock_agents.__enter__ = mock.Mock(return_value=mock_agents)
        mock_agents.__exit__ = mock.Mock(return_value=False)

        mock_issues = mock.MagicMock()
        mock_issues.read.return_value = json.dumps(issues_resp).encode()
        mock_issues.__enter__ = mock.Mock(return_value=mock_issues)
        mock_issues.__exit__ = mock.Mock(return_value=False)

        with mock.patch.dict("os.environ", env, clear=True):
            with mock.patch("urllib.request.urlopen", side_effect=[mock_agents, mock_issues]):
                result = main([
                    "check",
                    "--sprint-start", "2026-06-23",
                    "--sprint-end", "2026-07-06",
                ])
        assert result == 2

    def test_create_alerts_flag_creates_issues(self) -> None:
        env = {
            "PAPERCLIP_API_URL": "http://localhost:3100",
            "PAPERCLIP_API_KEY": "test-key",
            "PAPERCLIP_COMPANY_ID": "co-1",
            "PAPERCLIP_CTO_AGENT_ID": "cto-1",
        }

        agents_resp = {"agents": [{"id": "a1", "name": "Agent1"}]}
        issues_resp = {"issues": [
            {"id": f"i{i}", "status": "todo", "createdAt": "2026-06-24T10:00:00Z"}
            for i in range(6)
        ]}
        alert_resp = {"id": "alert-1", "identifier": "NFM-ALERT"}

        mock_agents = mock.MagicMock()
        mock_agents.read.return_value = json.dumps(agents_resp).encode()
        mock_agents.__enter__ = mock.Mock(return_value=mock_agents)
        mock_agents.__exit__ = mock.Mock(return_value=False)

        mock_issues = mock.MagicMock()
        mock_issues.read.return_value = json.dumps(issues_resp).encode()
        mock_issues.__enter__ = mock.Mock(return_value=mock_issues)
        mock_issues.__exit__ = mock.Mock(return_value=False)

        mock_alert = mock.MagicMock()
        mock_alert.read.return_value = json.dumps(alert_resp).encode()
        mock_alert.__enter__ = mock.Mock(return_value=mock_alert)
        mock_alert.__exit__ = mock.Mock(return_value=False)

        with mock.patch.dict("os.environ", env, clear=True):
            with mock.patch("urllib.request.urlopen", side_effect=[mock_agents, mock_issues, mock_alert]):
                result = main([
                    "check",
                    "--sprint-start", "2026-06-23",
                    "--sprint-end", "2026-07-06",
                    "--create-alerts",
                ])
        assert result == 2

    def test_empty_agents_returns_0(self) -> None:
        env = {
            "PAPERCLIP_API_URL": "http://localhost:3100",
            "PAPERCLIP_API_KEY": "test-key",
            "PAPERCLIP_COMPANY_ID": "co-1",
        }

        agents_resp = {"agents": []}
        mock_agents = mock.MagicMock()
        mock_agents.read.return_value = json.dumps(agents_resp).encode()
        mock_agents.__enter__ = mock.Mock(return_value=mock_agents)
        mock_agents.__exit__ = mock.Mock(return_value=False)

        with mock.patch.dict("os.environ", env, clear=True):
            with mock.patch("urllib.request.urlopen", return_value=mock_agents):
                result = main([
                    "check",
                    "--sprint-start", "2026-06-23",
                    "--sprint-end", "2026-07-06",
                ])
        assert result == 0
