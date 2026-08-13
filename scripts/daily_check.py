#!/usr/bin/env python3
"""NucPot daily inspection script.

Semi-automated daily health & compliance check covering:
  - GitHub Actions latest run status (requires GITHUB_TOKEN)
  - SSL certificate expiry for monitored domains
  - Site health via health_check.py

Usage:
    python scripts/daily_check.py
    GITHUB_TOKEN=ghp_... python scripts/daily_check.py

Exit code: 0 = all pass, 1 = one or more failures.

Environment variables:
    GITHUB_TOKEN    — GitHub personal-access token (for Actions check)
    ALERT_WEBHOOK   — Feishu/Lark webhook URL (optional alerts)
"""

from __future__ import annotations

import json
import os
import subprocess
import ssl
import sys
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SSL_MIN_DAYS = 30
SSL_DOMAINS = [
    "nucpot.dpdns.org",
    "verify.nucpot.dpdns.org",
]

# GitHub repo — used for Actions status check
GITHUB_REPO = os.environ.get("GITHUB_REPO", "NFM-DB/nfm-db")

HEALTH_CHECK_SCRIPT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "health_check.py"
)


# ---------------------------------------------------------------------------
# Check result model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InspectionItem:
    """A single inspection check item."""

    name: str
    passed: bool
    detail: str
    severity: str = "P1"


@dataclass
class InspectionReport:
    """Aggregated inspection results."""

    items: list[InspectionItem] = field(default_factory=list)
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    @property
    def all_passed(self) -> bool:
        return all(item.passed for item in self.items)

    @property
    def failures(self) -> list[InspectionItem]:
        return [item for item in self.items if not item.passed]


# ---------------------------------------------------------------------------
# SSL certificate check
# ---------------------------------------------------------------------------


def check_ssl_cert(domain: str, min_days: int = SSL_MIN_DAYS) -> InspectionItem:
    """Check SSL certificate expiry for a domain."""
    ctx = ssl.create_default_context()
    conn = None
    try:
        conn = ctx.wrap_socket(
            urllib.request.socket.create_connection((domain, 443), timeout=10),
            server_hostname=domain,
        )
        cert = conn.getpeercert()
        if not cert:
            return InspectionItem(
                name=f"SSL: {domain}",
                passed=False,
                detail="No certificate returned",
            )

        not_before = datetime.strptime(
            cert["notBefore"], "%b %d %H:%M:%S %Y %Z"
        ).replace(tzinfo=timezone.utc)
        not_after = datetime.strptime(
            cert["notAfter"], "%b %d %H:%M:%S %Y %Z"
        ).replace(tzinfo=timezone.utc)

        remaining = not_after - datetime.now(timezone.utc)
        remaining_days = remaining.days

        if remaining_days < 0:
            return InspectionItem(
                name=f"SSL: {domain}",
                passed=False,
                severity="P0",
                detail=f"EXPIRED {abs(remaining_days)} days ago",
            )
        if remaining_days < min_days:
            return InspectionItem(
                name=f"SSL: {domain}",
                passed=False,
                severity="P1",
                detail=f"Expires in {remaining_days} days (< {min_days} day threshold)",
            )

        return InspectionItem(
            name=f"SSL: {domain}",
            passed=True,
            detail=f"Valid for {remaining_days} more days (expires {not_after:%Y-%m-%d})",
        )
    except Exception as exc:  # noqa: BLE001
        return InspectionItem(
            name=f"SSL: {domain}",
            passed=False,
            detail=f"Connection error: {exc}",
        )
    finally:
        if conn:
            conn.close()


# ---------------------------------------------------------------------------
# GitHub Actions check
# ---------------------------------------------------------------------------


def check_github_actions() -> InspectionItem:
    """Check the latest GitHub Actions workflow run status."""
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        return InspectionItem(
            name="GitHub Actions",
            passed=True,
            detail="Skipped (GITHUB_TOKEN not set)",
            severity="P1",
        )

    url = f"https://api.github.com/repos/{GITHUB_REPO}/actions/runs?per_page=5"
    req = urllib.request.Request(url, method="GET")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "NucPot-DailyCheck/1.0")

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        return InspectionItem(
            name="GitHub Actions",
            passed=False,
            detail=f"API request failed: {exc}",
        )

    workflow_runs = data.get("workflow_runs", [])
    if not workflow_runs:
        return InspectionItem(
            name="GitHub Actions",
            passed=True,
            detail="No workflow runs found",
        )

    failures: list[str] = []
    for run in workflow_runs:
        name = run.get("name", "unknown")
        status = run.get("status", "")
        conclusion = run.get("conclusion", "")
        created = run.get("created_at", "")[:16]

        if status != "completed":
            failures.append(f"{name}: still running (started {created})")
        elif conclusion not in ("success", None):
            failures.append(f"{name}: {conclusion} (ran {created})")

    if failures:
        return InspectionItem(
            name="GitHub Actions",
            passed=False,
            detail="; ".join(failures),
        )

    last_run = workflow_runs[0]
    return InspectionItem(
        name="GitHub Actions",
        passed=True,
        detail=(
            f"Latest {len(workflow_runs)} runs all green "
            f"(last: {last_run.get('name', '?')} @ {last_run.get('created_at', '')[:16]})"
        ),
    )


# ---------------------------------------------------------------------------
# Health check (delegates to health_check.py)
# ---------------------------------------------------------------------------


def run_health_check() -> InspectionItem:
    """Run health_check.py and capture its exit code."""
    try:
        result = subprocess.run(
            [sys.executable, HEALTH_CHECK_SCRIPT],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0:
            return InspectionItem(
                name="Site Health",
                passed=True,
                detail="All endpoints healthy",
            )

        # Extract failure summary from stderr/stdout
        output = result.stderr.strip() or result.stdout.strip()
        # Take last few lines for brevity
        lines = output.split("\n")
        summary = "\n".join(lines[-5:]) if len(lines) > 5 else output
        return InspectionItem(
            name="Site Health",
            passed=False,
            severity="P0",
            detail=f"Health check failed (exit {result.returncode}):\n{summary}",
        )
    except subprocess.TimeoutExpired:
        return InspectionItem(
            name="Site Health",
            passed=False,
            severity="P0",
            detail="Health check timed out after 60s",
        )
    except FileNotFoundError:
        return InspectionItem(
            name="Site Health",
            passed=True,
            detail="Skipped (health_check.py not found)",
        )
    except Exception as exc:  # noqa: BLE001
        return InspectionItem(
            name="Site Health",
            passed=False,
            detail=f"Failed to run health_check.py: {exc}",
        )


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def format_report(report: InspectionReport) -> str:
    """Format inspection results as human-readable markdown."""
    lines = [
        f"## NucPot Daily Inspection — {report.timestamp[:16].replace('T', ' ')} UTC",
        "",
    ]

    if report.all_passed:
        lines.append("**All checks PASSED** ✅")
    else:
        lines.append(
            f"**{len(report.failures)}/{len(report.items)} check(s) FAILED** ❌"
        )

    lines.append("")
    lines.append("| # | Check | Status | Severity | Detail |")
    lines.append("|---|-------|--------|----------|--------|")

    for idx, item in enumerate(report.items, start=1):
        icon = "✅" if item.passed else "❌"
        lines.append(
            f"| {idx} | {item.name} | {icon} | {item.severity} | {item.detail} |"
        )

    lines.append("")
    if report.failures:
        p0_count = sum(1 for f in report.failures if f.severity == "P0")
        p1_count = sum(1 for f in report.failures if f.severity == "P1")
        lines.append(f"**Failures**: {p0_count} P0, {p1_count} P1")
        lines.append("")
        lines.append("### Action Required")
        for f in report.failures:
            urgency = "🔴 URGENT" if f.severity == "P0" else "🟡 ATTENTION"
            lines.append(f"- {urgency} — {f.name}: {f.detail}")

    return "\n".join(lines)


def send_alert(report: InspectionReport, webhook_url: str) -> None:
    """Send Feishu/Lark webhook alert for failed inspections."""
    if report.all_passed:
        return

    failure_lines = [
        f"**{f.name}** ({f.severity}): {f.detail}"
        for f in report.failures
    ]
    alert_level = (
        "🔴 P0 CRITICAL"
        if any(f.severity == "P0" for f in report.failures)
        else "🟡 P1 WARNING"
    )
    content_text = (
        f"{alert_level}\n\n"
        f"**NucPot Daily Inspection Alert**\n"
        f"Time: {report.timestamp[:16].replace('T', ' ')} UTC\n"
        f"Failed: {len(report.failures)}/{len(report.items)} checks\n\n"
        + "\n".join(failure_lines)
    )

    payload: dict[str, Any] = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": (
                        f"NucPot Inspection: {len(report.failures)} check(s) failed"
                    ),
                },
                "template": (
                    "red"
                    if any(f.severity == "P0" for f in report.failures)
                    else "orange"
                ),
            },
            "elements": [
                {"tag": "markdown", "content": content_text},
            ],
        },
    }

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(webhook_url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(req, timeout=10):
            pass
    except Exception as exc:  # noqa: BLE001
        print(f"Failed to send alert: {exc}", file=sys.stderr)


def write_github_output(report: InspectionReport) -> None:
    """Write GitHub Actions output variables for downstream steps."""
    gh_output = os.environ.get("GITHUB_OUTPUT")
    if not gh_output:
        return

    with open(gh_output, "a", encoding="utf-8") as fh:
        fh.write(f"all_passed={str(report.all_passed).lower()}\n")
        fh.write(f"total_checks={len(report.items)}\n")
        fh.write(f"failed_checks={len(report.failures)}\n")
        p0_count = sum(1 for f in report.failures if f.severity == "P0")
        fh.write(f"p0_failures={p0_count}\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    """Run all daily inspections and output the report."""
    print("Starting NucPot daily inspection...")
    print("")

    report = InspectionReport()

    # 1. GitHub Actions status
    print("  Checking GitHub Actions...", end=" ", flush=True)
    report.items.append(check_github_actions())
    print("done")

    # 2. SSL certificates
    print("  Checking SSL certificates...", end=" ", flush=True)
    for domain in SSL_DOMAINS:
        report.items.append(check_ssl_cert(domain))
    print("done")

    # 3. Site health (delegates to health_check.py)
    print("  Running health_check.py...", end=" ", flush=True)
    report.items.append(run_health_check())
    print("done")

    # Print report
    print("")
    print(format_report(report))

    # Send alerts if configured
    webhook_url = os.environ.get("ALERT_WEBHOOK", "")
    if webhook_url:
        send_alert(report, webhook_url)
        print("\nAlert sent to webhook.")

    # GitHub Actions output
    write_github_output(report)

    # Exit code
    if report.failures:
        p0 = sum(1 for f in report.failures if f.severity == "P0")
        p1 = sum(1 for f in report.failures if f.severity == "P1")
        print(f"\n⚠️  {len(report.failures)} check(s) failed ({p0} P0, {p1} P1)")
        return 1

    print("\n✅ All daily checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
