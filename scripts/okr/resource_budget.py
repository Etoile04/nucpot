#!/usr/bin/env python3
"""Resource budget enforcement tool for NFM Sprint management.

Monitors per-agent Sprint issue creation limits and WIP (Work In Progress)
caps. When an agent exceeds budget, optionally creates a Paperclip alert
issue assigned to the CTO for review.

Usage:
    python scripts/okr/resource_budget.py check \
        --sprint-start 2026-06-23 \
        --sprint-end 2026-07-06

    python scripts/okr/resource_budget.py check \
        --sprint-start 2026-06-23 \
        --sprint-end 2026-07-06 \
        --create-alerts

Environment variables:
    PAPERCLIP_API_URL   — Paperclip server base URL
    PAPERCLIP_API_KEY   — Bearer token for Paperclip API
    PAPERCLIP_COMPANY_ID — Company identifier
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BudgetConfig:
    """Sprint budget thresholds and date range."""

    sprint_start: date
    sprint_end: date
    creation_budget: int
    wip_budget: int


@dataclass(frozen=True)
class AgentBudget:
    """Per-agent budget usage snapshot."""

    agent_id: str
    agent_name: str
    created_this_sprint: int
    creation_budget: int
    creation_used: float
    in_progress: int
    wip_budget: int
    wip_used: float
    over_budget: bool


@dataclass(frozen=True)
class BudgetReport:
    """Aggregated budget report for a sprint."""

    sprint_start: date
    sprint_end: date
    agents: tuple[AgentBudget, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# CLI Argument Parsing
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments for the resource budget tool."""
    parser = argparse.ArgumentParser(
        description="Check sprint resource budgets for NFM agents.",
    )
    subparsers = parser.add_subparsers(dest="command")
    check_parser = subparsers.add_parser("check", help="Run budget check for a sprint")
    check_parser.add_argument(
        "--sprint-start",
        required=True,
        type=date.fromisoformat,
        help="Sprint start date (YYYY-MM-DD, inclusive)",
    )
    check_parser.add_argument(
        "--sprint-end",
        required=True,
        type=date.fromisoformat,
        help="Sprint end date (YYYY-MM-DD, inclusive)",
    )
    check_parser.add_argument(
        "--creation-budget",
        type=int,
        default=5,
        help="Max issues per agent per sprint (default: 5)",
    )
    check_parser.add_argument(
        "--wip-budget",
        type=int,
        default=3,
        help="Max concurrent in_progress issues per agent (default: 3)",
    )
    check_parser.add_argument(
        "--create-alerts",
        action="store_true",
        default=False,
        help="Create Paperclip alert issues for over-budget agents",
    )
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# Date Parsing Helpers
# ---------------------------------------------------------------------------


def _parse_created_at(raw: str) -> date | None:
    """Extract the date portion from an ISO-8601 createdAt string.

    Returns None if the string cannot be parsed.
    """
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).date()
    except (ValueError, AttributeError):
        return None


# ---------------------------------------------------------------------------
# Budget Calculation
# ---------------------------------------------------------------------------


def count_created_this_sprint(
    issues: list[dict[str, Any]],
    config: BudgetConfig,
) -> int:
    """Count issues created between sprint_start (inclusive) and sprint_end."""
    count = 0
    for issue in issues:
        created_date = _parse_created_at(issue.get("createdAt", ""))
        if created_date is None:
            continue
        if config.sprint_start <= created_date <= config.sprint_end:
            count += 1
    return count


def count_in_progress(issues: list[dict[str, Any]]) -> int:
    """Count issues currently in the in_progress state."""
    return sum(1 for i in issues if i.get("status") == "in_progress")


def check_budget(
    agent_issues: list[dict[str, Any]],
    agent_id: str,
    agent_name: str,
    config: BudgetConfig,
) -> AgentBudget:
    """Compute budget usage for a single agent."""
    created = count_created_this_sprint(agent_issues, config)
    wip = count_in_progress(agent_issues)

    creation_used = created / config.creation_budget if config.creation_budget else 0.0
    wip_used = wip / config.wip_budget if config.wip_budget else 0.0

    over = created > config.creation_budget or wip > config.wip_budget

    return AgentBudget(
        agent_id=agent_id,
        agent_name=agent_name,
        created_this_sprint=created,
        creation_budget=config.creation_budget,
        creation_used=round(creation_used, 3),
        in_progress=wip,
        wip_budget=config.wip_budget,
        wip_used=round(wip_used, 3),
        over_budget=over,
    )


# ---------------------------------------------------------------------------
# Paperclip API Interaction
# ---------------------------------------------------------------------------


def _api_get(
    api_url: str,
    company_id: str,
    api_key: str,
    path: str,
    params: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Make an authenticated GET request to the Paperclip API.

    Returns parsed JSON or empty dict on failure.
    """
    base = f"{api_url}/api/companies/{company_id}{path}"
    if params:
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        base = f"{base}?{qs}"

    req = urllib.request.Request(base, method="GET")
    req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Accept", "application/json")

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        return {}


def fetch_all_agents(
    api_url: str,
    company_id: str,
    api_key: str,
) -> list[dict[str, Any]]:
    """Fetch all agents from the Paperclip API.

    Returns a list of agent dicts, each with at least 'id' and 'name'.
    Returns empty list on API failure.
    """
    data = _api_get(api_url, company_id, api_key, "/agents")
    if isinstance(data, list):
        return data
    return data.get("agents", [])


def fetch_agent_issues(
    api_url: str,
    company_id: str,
    agent_id: str,
    api_key: str,
) -> list[dict[str, Any]]:
    """Fetch all issues assigned to a specific agent.

    Returns a list of issue dicts. Returns empty list on API failure.
    """
    data = _api_get(
        api_url,
        company_id,
        api_key,
        "/issues",
        params={"assigneeAgentId": agent_id},
    )
    if isinstance(data, list):
        return data
    return data.get("issues", [])


# ---------------------------------------------------------------------------
# Alert Issue Creation
# ---------------------------------------------------------------------------


def build_alert_title(budget: AgentBudget) -> str:
    """Build the title for a budget alert issue."""
    return f"[BUDGET ALERT] {budget.agent_name} exceeded Sprint resource budget"


def build_alert_body(
    budget: AgentBudget,
    sprint_start: date,
    sprint_end: date,
) -> str:
    """Build the markdown body for a budget alert issue."""
    lines = [
        f"## Sprint Budget Alert: {budget.agent_name}",
        "",
        f"**Sprint Period:** {sprint_start.isoformat()} — {sprint_end.isoformat()}",
        "",
        "### Creation Budget",
        f"- Created this sprint: **{budget.created_this_sprint}** / {budget.creation_budget}",
        f"- Utilization: **{budget.creation_used:.1%}**",
        "",
        "### WIP Budget",
        f"- In-progress issues: **{budget.in_progress}** / {budget.wip_budget}",
        f"- Utilization: **{budget.wip_used:.1%}**",
        "",
        "> **Action Required:** CTO review and approval needed for over-budget allocation.",
    ]

    if budget.created_this_sprint > budget.creation_budget:
        lines.append("")
        lines.append(
            f"⚠️ **Creation over by {budget.created_this_sprint - budget.creation_budget} issue(s).**"
        )

    if budget.in_progress > budget.wip_budget:
        lines.append(
            f"⚠️ **WIP over by {budget.in_progress - budget.wip_budget} issue(s).**"
        )

    return "\n".join(lines)


def create_budget_alert(
    api_url: str,
    company_id: str,
    api_key: str,
    budget: AgentBudget,
    config: BudgetConfig,
    cto_agent_id: str,
    parent_issue_id: str | None = None,
) -> dict[str, Any] | None:
    """Create a budget alert issue in Paperclip assigned to the CTO.

    Returns the created issue dict on success, None on failure.
    """
    payload: dict[str, Any] = {
        "title": build_alert_title(budget),
        "description": build_alert_body(budget, config.sprint_start, config.sprint_end),
        "priority": "high",
        "assigneeAgentId": cto_agent_id,
    }
    if parent_issue_id:
        payload["parentId"] = parent_issue_id

    url = f"{api_url}/api/companies/{company_id}/issues"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        return None


# ---------------------------------------------------------------------------
# Report Formatting
# ---------------------------------------------------------------------------


def format_report(report: BudgetReport) -> str:
    """Format the budget report as JSON suitable for Weekly Report consumption."""
    agents_data = [
        {
            "agentId": a.agent_id,
            "agentName": a.agent_name,
            "createdThisSprint": a.created_this_sprint,
            "creationBudget": a.creation_budget,
            "creationUsed": a.creation_used,
            "inProgress": a.in_progress,
            "wipBudget": a.wip_budget,
            "wipUsed": a.wip_used,
            "overBudget": a.over_budget,
        }
        for a in report.agents
    ]

    over_count = sum(1 for a in report.agents if a.over_budget)

    return json.dumps(
        {
            "sprint": {
                "start": report.sprint_start.isoformat(),
                "end": report.sprint_end.isoformat(),
            },
            "agents": agents_data,
            "summary": {
                "totalAgents": len(report.agents),
                "overBudgetAgents": over_count,
            },
        },
        indent=2,
    )


# ---------------------------------------------------------------------------
# Main Entry Point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Run the resource budget check and output the report."""
    args = parse_args(argv)

    if args.command != "check":
        print("Error: expected 'check' subcommand. See --help.", file=sys.stderr)
        return 1

    api_url = os.environ.get("PAPERCLIP_API_URL", "")
    api_key = os.environ.get("PAPERCLIP_API_KEY", "")
    company_id = os.environ.get("PAPERCLIP_COMPANY_ID", "")

    if not all([api_url, api_key, company_id]):
        print(
            "Error: PAPERCLIP_API_URL, PAPERCLIP_API_KEY, "
            "and PAPERCLIP_COMPANY_ID must be set.",
            file=sys.stderr,
        )
        return 1

    config = BudgetConfig(
        sprint_start=args.sprint_start,
        sprint_end=args.sprint_end,
        creation_budget=args.creation_budget,
        wip_budget=args.wip_budget,
    )

    # Fetch all agents
    agents = fetch_all_agents(api_url, company_id, api_key)
    if not agents:
        print("Warning: No agents found or API error.", file=sys.stderr)

    # Check budget per agent
    budgets: list[AgentBudget] = []
    for agent in agents:
        agent_id = agent.get("id", "")
        agent_name = agent.get("name", agent_id)
        issues = fetch_agent_issues(api_url, company_id, agent_id, api_key)
        budget = check_budget(issues, agent_id, agent_name, config)
        budgets.append(budget)

    report = BudgetReport(
        sprint_start=config.sprint_start,
        sprint_end=config.sprint_end,
        agents=tuple(budgets),
    )

    # Output JSON report
    print(format_report(report))

    # Create alerts for over-budget agents
    if args.create_alerts:
        cto_id = os.environ.get("PAPERCLIP_CTO_AGENT_ID", "")
        parent_id = os.environ.get("PAPERCLIP_PARENT_ISSUE_ID")
        for budget in budgets:
            if budget.over_budget:
                result = create_budget_alert(
                    api_url, company_id, api_key, budget, config,
                    cto_agent_id=cto_id, parent_issue_id=parent_id,
                )
                if result:
                    print(
                        f"Alert created: {result.get('identifier', 'unknown')}",
                        file=sys.stderr,
                    )
                else:
                    print(
                        f"Failed to create alert for {budget.agent_name}",
                        file=sys.stderr,
                    )

    # Exit code: 2 if any agent is over budget
    if any(b.over_budget for b in budgets):
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
